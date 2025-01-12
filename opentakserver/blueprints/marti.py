import base64
import hashlib
import json
import os
import datetime
import time
import traceback
import uuid
from urllib.parse import urlparse

import jwt

from xml.etree.ElementTree import Element, tostring, fromstring, SubElement

import bleach
import sqlalchemy
from OpenSSL import crypto
from bs4 import BeautifulSoup
from flask import current_app as app, request, Blueprint, send_from_directory, jsonify
from flask_security import verify_password, current_user
from opentakserver.extensions import logger, db
from opentakserver.forms.MediaMTXPathConfig import MediaMTXPathConfig
from opentakserver import __version__ as version

from opentakserver.models.EUD import EUD
from opentakserver.models.DataPackage import DataPackage
from werkzeug.utils import secure_filename

from opentakserver.models.VideoStream import VideoStream

from opentakserver.certificate_authority import CertificateAuthority

from opentakserver.models.Certificate import Certificate

marti_blueprint = Blueprint('marti_blueprint', __name__)


# flask-security's http_auth_required() decorator will deny access because ATAK doesn't do CSRF,
# so we handle basic auth ourselves
def basic_auth(credentials):
    try:
        username, password = base64.b64decode(credentials.split(" ")[-1].encode('utf-8')).decode('utf-8').split(":")
        username = bleach.clean(username)
        password = bleach.clean(password)
        user = app.security.datastore.find_user(username=username)
        return user and verify_password(password, user.password)
    except BaseException as e:
        logger.error("Failed to verify credentials: {}".format(e))
        return False


@marti_blueprint.route('/Marti/api/clientEndPoints', methods=['GET'])
def client_end_points():
    euds = db.session.execute(db.select(EUD)).scalars()
    return_value = {'version': 3, "type": "com.bbn.marti.remote.ClientEndpoint", 'data': [],
                    'nodeId': app.config.get("OTS_NODE_ID")}
    for eud in euds:
        return_value['data'].append({
            'callsign': eud.callsign,
            'uid': eud.uid,
            'username': current_user.username if current_user.is_authenticated else 'anonymous',
            'lastEventTime': eud.last_event_time,
            'lastStatus': eud.last_status
        })

    return return_value, 200, {'Content-Type': 'application/json'}


# require basic auth
@marti_blueprint.route('/Marti/api/tls/config')
def tls_config():
    if not basic_auth(request.headers.get('Authorization')):
        return '', 401

    root_element = Element('ns2:certificateConfig')
    root_element.set('xmlns', "http://bbn.com/marti/xml/config")
    root_element.set('xmlns:ns2', "com.bbn.marti.config")

    name_entries = SubElement(root_element, "nameEntries")
    first_name_entry = SubElement(name_entries, "nameEntry")
    first_name_entry.set('name', 'O')
    first_name_entry.set('value', 'Test Organization Name')

    second_name_entry = SubElement(name_entries, "nameEntry")
    second_name_entry.set('name', 'OU')
    second_name_entry.set('value', 'Test Organization Unit Name')

    return tostring(root_element), 200, {'Content-Type': 'application/xml'}


@marti_blueprint.route('/Marti/api/tls/profile/enrollment')
def enrollment():
    if not basic_auth(request.headers.get('Authorization')):
        return '', 401
    logger.info("Enrollment request from {}".format(request.args.get('clientUid')))
    return '', 204


@marti_blueprint.route('/Marti/api/tls/signClient/', methods=['POST'])
def sign_csr():
    if not basic_auth(request.headers.get('Authorization')):
        return '', 401
    return '', 200


@marti_blueprint.route('/Marti/api/tls/signClient/v2', methods=['POST'])
def sign_csr_v2():
    if not basic_auth(request.headers.get('Authorization')):
        return '', 401

    try:
        uid = request.args.get("clientUid")

        if "iTAK" not in request.user_agent.string:
            csr = '-----BEGIN CERTIFICATE REQUEST-----\n' + request.data.decode(
                'utf-8') + '-----END CERTIFICATE REQUEST-----'
        else:
            csr = request.data.decode('utf-8')

        x509 = crypto.load_certificate_request(crypto.FILETYPE_PEM, csr.encode())
        common_name = x509.get_subject().CN
        logger.debug("Attempting to sign CSR for {}".format(common_name))

        cert_authority = CertificateAuthority(logger, app)

        signed_csr = cert_authority.sign_csr(csr.encode(), common_name, False).decode("utf-8")
        signed_csr = signed_csr.replace("-----BEGIN CERTIFICATE-----\n", "")
        signed_csr = signed_csr.replace("\n-----END CERTIFICATE-----\n", "")

        f = open(os.path.join(app.config.get("OTS_CA_FOLDER"), "ca.pem"), 'r')
        cert = f.read()
        f.close()

        cert = cert.replace("-----BEGIN CERTIFICATE-----\n", "")
        cert = cert.replace("\n-----END CERTIFICATE-----\n", "")

        if "iTAK" in request.user_agent.string:
            response = {'signedCert': signed_csr, 'ca0': cert, 'ca1': cert}
        else:
            enrollment = Element('enrollment')
            signed_cert = SubElement(enrollment, 'signedCert')
            signed_cert.text = signed_csr
            ca = SubElement(enrollment, 'ca')
            ca.text = cert

            response = tostring(enrollment).decode('utf-8')
            response = '<?xml version="1.0" encoding="UTF-8"?>\n' + response

        username, password = base64.b64decode(
            request.headers.get("Authorization").split(" ")[-1].encode('utf-8')).decode(
            'utf-8').split(":")
        username = bleach.clean(username)
        user = app.security.datastore.find_user(username=username)

        try:
            eud = EUD()
            eud.uid = uid
            eud.user_id = user.id

            db.session.add(eud)
            db.session.commit()
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
            eud = db.session.execute(db.session.query(EUD).filter_by(uid=uid)).first()[0]
            if user and not eud.user_id:
                eud.user_id = user.id
                db.session.add(eud)
                db.session.commit()

        try:
            certificate = Certificate()
            certificate.common_name = common_name
            certificate.eud_uid = uid
            certificate.callsign = eud.callsign
            certificate.expiration_date = datetime.datetime.today() + datetime.timedelta(
                days=app.config.get("OTS_CA_EXPIRATION_TIME"))
            certificate.server_address = urlparse(request.url_root).hostname
            certificate.server_port = app.config.get("OTS_MARTI_HTTPS_PORT")
            certificate.truststore_filename = os.path.join(app.config.get("OTS_CA_FOLDER"), "truststore-root.p12")
            certificate.user_cert_filename = os.path.join(app.config.get("OTS_CA_FOLDER"), "certs", common_name,
                                                          common_name + ".pem")
            certificate.csr = os.path.join(app.config.get("OTS_CA_FOLDER"), "certs", common_name, common_name + ".csr")
            certificate.cert_password = app.config.get("OTS_CA_PASSWORD")

            db.session.add(certificate)
            db.session.commit()
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
            certificate = db.session.execute(db.session.query(Certificate).filter_by(eud_uid=eud.uid)).scalar_one()
            certificate.common_name = common_name
            certificate.callsign = eud.callsign
            certificate.expiration_date = datetime.datetime.today() + datetime.timedelta(
                days=app.config.get("OTS_CA_EXPIRATION_TIME"))
            certificate.server_address = urlparse(request.url_root).hostname
            certificate.server_port = app.config.get("OTS_MARTI_HTTPS_PORT")
            certificate.truststore_filename = os.path.join(app.config.get("OTS_CA_FOLDER"), "truststore-root.p12")
            certificate.user_cert_filename = os.path.join(app.config.get("OTS_CA_FOLDER"), "certs", common_name,
                                                          common_name + ".pem")
            certificate.csr = os.path.join(app.config.get("OTS_CA_FOLDER"), "certs", common_name, common_name + ".csr")
            certificate.cert_password = app.config.get("OTS_CA_PASSWORD")

            db.session.commit()

        if "iTAK" in request.user_agent.string:
            return response, 200, {'Content-Type': 'text/plain', 'Content-Encoding': 'charset=UTF-8'}
        else:
            return response, 200, {'Content-Type': 'application/xml', 'Content-Encoding': 'charset=UTF-8'}
    except BaseException as e:
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@marti_blueprint.route('/Marti/api/version/config', methods=['GET'])
def marti_config():
    url = urlparse(request.url_root)

    return {"version": "3", "type": "ServerConfig",
            "data": {"version": version, "api": "3", "hostname": url.hostname},
            "nodeId": app.config.get("OTS_NODE_ID")}, 200, {'Content-Type': 'application/json'}


@marti_blueprint.route('/Marti/api/missions/citrap/subscription', methods=['PUT'])
def citrap_subscription():
    uid = bleach.clean(request.args.get('uid'))
    response = {
        'version': 3, 'type': 'com.bbn.marti.sync.model.MissionSubscription',
        'data': {

        }
    }
    return '', 201


@marti_blueprint.route('/Marti/api/missions/invitations')
def mission_invitations():
    uid = bleach.clean(request.args.get('clientUid'))
    response = {
        'version': 3, 'type': 'MissionInvitation', 'data': [], 'nodeId': app.config.get('OTS_NODE_ID')
    }

    return jsonify(response)


@marti_blueprint.route('/Marti/api/missions')
def missions():
    password_protected = request.args.get('passwordProtected')
    if password_protected:
        password_protected = bleach.clean(password_protected).lower() == 'true'

    default_role = request.args.get('defaultRole')
    if default_role:
        default_role = bleach.clean(default_role).lower() == 'true'

    response = {
        'version': 3, 'type': 'Mission', 'data': [], 'nodeId': app.config.get('OTS_NODE_ID')
    }

    return jsonify(response)


@marti_blueprint.route('/Marti/api/groups/all')
def groups():
    use_cache = bleach.clean(request.args.get('useCache'))  # bool

    response = {
        'version': 3, 'type': 'com.bbn.marti.remote.groups.Group', 'data': [{}], 'nodeId': app.config.get("OTS_NODE_ID")
    }

    return jsonify(response)


@marti_blueprint.route('/Marti/api/missions/all/invitations')
def all_invitations():
    clientUid = bleach.clean(request.args.get('clientUid'))
    return '', 200


@marti_blueprint.route('/Marti/api/missions/<mission_name>', methods=['GET', 'PUT'])
def put_mission(mission_name):
    if request.method == 'PUT':
        creator_uid = bleach.clean(request.args.get('creatorUid'))
        description = bleach.clean(request.args.get('description'))
        tool = bleach.clean(request.args.get('tool'))
        group = bleach.clean(request.args.get('group'))
        default_role = bleach.clean(request.args.get('defaultRole'))
        password = request.args.get('password')
        password_protected = False
        if password:
            password = bleach.clean(password)
            password_protected = True

        uid = str(uuid.uuid4())
        creation_time = int(time.time())
        creation_datetime = datetime.datetime.now()

        payload = {'jti': uid, 'iat': creation_time, 'sub': 'SUBSCRIPTION', 'iss': '',
                   'SUBSCRIPTION': uid, 'MISSION_NAME': mission_name}

        server_key = open(os.path.join(app.config.get("OTS_CA_FOLDER"), "certs", "opentakserver",
                                       "opentakserver.nopass.key"), "rb")

        token = jwt.encode(payload, server_key.read(), algorithm="HS256")
        server_key.close()

        response = {
            'version': 3, 'type': 'Mission', 'data': [{
                'name': mission_name, 'description': description, 'chatRoom': '', 'baseLayer': '', 'path': '',
                'classification': '',
                'tool': tool, 'keywords': [], 'creatorUid': creator_uid, 'createTime': creation_datetime,
                'externalData': [],
                'feeds': [], 'mapLayers': [], 'defaultRole': {
                    'permissions': ["MISSION_WRITE", "MISSION_READ"], "type": "MISSION_SUBSCRIBER"},

                'ownerRole': {"permissions": ["MISSION_MANAGE_FEEDS", "MISSION_SET_PASSWORD", "MISSION_WRITE",
                                              "MISSION_MANAGE_LAYERS", "MISSION_UPDATE_GROUPS", "MISSION_SET_ROLE",
                                              "MISSION_READ", "MISSION_DELETE"], "type": "MISSION_OWNER"},
                'inviteOnly': False, 'expiration': -1, 'guid': '', 'uids': [], 'contents': [], 'token': token,
                'passwordProtected': password_protected, 'nodeId': app.config.get("OTS_NODE_ID")
            }]
        }

        return jsonify(response), 201
    elif request.method == 'GET':
        # Pull it from the DB

        return '', 200


@marti_blueprint.route('/Marti/api/missions/<mission_name>/keywords', methods=['PUT'])
def put_mission_keywords(mission_name):
    keywords = request.json()

    return '', 200


@marti_blueprint.route('/Marti/api/missions/<mission_name>/subscription', methods=['PUT'])
def mission_subscribe(mission_name):
    uid = bleach.clean(request.args.get("uid"))

    return '', 200


@marti_blueprint.route('/Marti/api/citrap')
def citrap():
    return jsonify([])


@marti_blueprint.route('/Marti/api/groups/groupCacheEnabled')
def group_cache_enabled():
    response = {
        'version': 3, 'type': 'java.lang.Boolean', 'data': False, 'nodeId': app.config.get('OTS_NODE_ID')
    }

    return jsonify(response)


@marti_blueprint.route('/Marti/sync/upload', methods=['POST'])
def itak_data_package_upload():
    if not request.content_length:
        return {'error': 'no file'}, 400, {'Content-Type': 'application/json'}
    elif request.content_type != 'application/x-zip-compressed':
        logger.error("Not a zip")
        return {'error': 'Please only upload zip files'}, 415, {'Content-Type': 'application/json'}

    file = request.data
    sha256 = hashlib.sha256()
    sha256.update(file)
    file_hash = sha256.hexdigest()
    logger.debug("got sha256 {}".format(file_hash))
    hash_filename = secure_filename(file_hash + '.zip')

    with open(os.path.join(app.config.get("UPLOAD_FOLDER"), hash_filename), "wb") as f:
        f.write(file)

    try:
        data_package = DataPackage()
        data_package.filename = request.args.get('name')
        data_package.hash = file_hash
        data_package.creator_uid = request.args.get('CreatorUid') if request.args.get('CreatorUid') else str(
            uuid.uuid4())
        data_package.submission_user = current_user.id if current_user.is_authenticated else None
        data_package.submission_time = datetime.datetime.now()
        data_package.mime_type = request.content_type
        data_package.size = os.path.getsize(os.path.join(app.config.get("UPLOAD_FOLDER"), hash_filename))
        db.session.add(data_package)
        db.session.commit()
    except sqlalchemy.exc.IntegrityError as e:
        db.session.rollback()
        logger.error("Failed to save data package: {}".format(e))
        return jsonify({'success': False, 'error': 'This data package has already been uploaded'}), 400

    return_value = {"UID": data_package.hash, "SubmissionDateTime": data_package.submission_time,
                    "Keywords": ["missionpackage"],
                    "MIMEType": data_package.mime_type, "SubmissionUser": "anonymous", "PrimaryKey": "1",
                    "Hash": data_package.hash, "CreatorUid": data_package.creator_uid, "Name": data_package.filename}

    return jsonify(return_value)


@marti_blueprint.route('/Marti/sync/missionupload', methods=['POST'])
def data_package_share():
    if not len(request.files):
        return {'error': 'no file'}, 400, {'Content-Type': 'application/json'}
    for file in request.files:
        file = request.files[file]

        if file.content_type != 'application/x-zip-compressed' and file.content_type != "application/zip-compressed" \
                and file.content_type != "application/zip" and not file.content_type.startswith("application/x-zip"):
            logger.error("Uploaded data package does not seem to be a zip file. The content type is {}".format(
                file.content_type))
            return {'error': "Uploaded data package does not seem to be a zip file. The content type is {}".format(
                file.content_type)}, 415, {'Content-Type': 'application/json'}

        if file:
            file_hash = request.args.get('hash')
            if not file_hash:
                sha256 = hashlib.sha256()
                sha256.update(file.stream.read())
                file.stream.seek(0)
                file_hash = sha256.hexdigest()
                logger.debug("got sha256 {}".format(file_hash))

            logger.debug("Got file: {} - {}".format(file.filename, file_hash))

            filename = secure_filename(file_hash + '.zip')
            file.save(os.path.join(app.config.get("UPLOAD_FOLDER"), filename))

            try:
                data_package = DataPackage()
                data_package.filename = file.filename
                data_package.hash = file_hash
                data_package.creator_uid = request.args.get('creatorUid') if request.args.get('creatorUid') else str(
                    uuid.uuid4())
                data_package.submission_user = current_user.id if current_user.is_authenticated else None
                data_package.submission_time = datetime.datetime.now()
                data_package.mime_type = file.mimetype
                data_package.size = os.path.getsize(os.path.join(app.config.get("UPLOAD_FOLDER"), filename))
                db.session.add(data_package)
                db.session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                db.session.rollback()
                logger.error("Failed to save data package: {}".format(e))
                return jsonify({'success': False, 'error': 'This data package has already been uploaded'}), 400

            url = urlparse(request.url_root)
            return 'https://{}:{}/Marti/api/sync/metadata/{}/tool'.format(url.hostname,
                                                                          app.config.get("OTS_MARTI_HTTPS_PORT"),
                                                                          file_hash), 200

        else:
            return jsonify({'success': False, 'error': 'Something went wrong'}), 400


@marti_blueprint.route('/Marti/api/sync/metadata/<file_hash>/tool', methods=['GET', 'PUT'])
def data_package_metadata(file_hash):
    if request.method == 'PUT':
        try:
            data_package = db.session.execute(db.select(DataPackage).filter_by(hash=file_hash)).scalar_one()
            if data_package:
                data_package.keywords = bleach.clean(request.data.decode("utf-8"))
                db.session.add(data_package)
                db.session.commit()
                return '', 200
            else:
                return '', 404
        except BaseException as e:
            logger.error("Data package PUT failed: {}".format(e))
            logger.error(traceback.format_exc())
            return {'error': str(e)}, 500
    elif request.method == 'GET':
        data_package = db.session.execute(db.select(DataPackage).filter_by(hash=file_hash)).scalar_one()
        return send_from_directory(app.config.get("UPLOAD_FOLDER"), data_package.hash + ".zip",
                                   download_name=data_package.filename)


@marti_blueprint.route('/Marti/sync/missionquery')
def data_package_query():
    try:
        data_package = db.session.execute(db.select(DataPackage).filter_by(hash=request.args.get('hash'))).scalar_one()
        if data_package:

            url = urlparse(request.url_root)
            return 'https://{}:{}/Marti/api/sync/metadata/{}/tool'.format(url.hostname,
                                                                          app.config.get("OTS_MARTI_HTTPS_PORT"),
                                                                          request.args.get('hash')), 200
        else:
            return {'error': '404'}, 404, {'Content-Type': 'application/json'}
    except sqlalchemy.exc.NoResultFound as e:
        return {'error': '404'}, 404, {'Content-Type': 'application/json'}


@marti_blueprint.route('/Marti/sync/search', methods=['GET'])
def data_package_search():
    data_packages = db.session.execute(db.select(DataPackage)).scalars()
    res = {'resultCount': 0, 'results': []}
    for dp in data_packages:
        submission_user = "anonymous"
        if dp.user:
            submission_user = dp.user.username
        res['results'].append(
            {'UID': dp.hash, 'Name': dp.filename, 'Hash': dp.hash, 'CreatorUid': dp.creator_uid,
             "SubmissionDateTime": dp.submission_time.strftime('%Y-%m-%dT%H:%M:%S.000Z'), "EXPIRATION": "-1",
             "Keywords": ["missionpackage"],
             "MIMEType": dp.mime_type, "Size": "{}".format(dp.size), "SubmissionUser": submission_user,
             "PrimaryKey": "{}".format(dp.id),
             "Tool": dp.tool if dp.tool else "public"
             })
        res['resultCount'] += 1

    return json.dumps(res), 200, {'Content-Type': 'application/json'}


@marti_blueprint.route('/Marti/sync/content', methods=['GET'])
def download_data_package():
    file_hash = request.args.get('hash')
    data_package = db.session.execute(db.select(DataPackage).filter_by(hash=file_hash)).scalar_one()

    return send_from_directory(app.config.get("UPLOAD_FOLDER"), file_hash + ".zip", download_name=data_package.filename)


@marti_blueprint.route('/Marti/vcm', methods=['GET', 'POST'])
def video():
    if request.method == 'POST':
        soup = BeautifulSoup(request.data, 'xml')
        video_connections = soup.find('videoConnections')

        path = video_connections.find('path').text
        if path.startswith("/"):
            path = path[1:]

        if video_connections:
            v = VideoStream()
            v.protocol = video_connections.find('protocol').text
            v.alias = video_connections.find('alias').text
            v.uid = video_connections.find('uid').text
            v.port = video_connections.find('port').text
            v.rover_port = video_connections.find('roverPort').text
            v.ignore_embedded_klv = (video_connections.find('ignoreEmbeddedKLV').text.lower() == 'true')
            v.preferred_mac_address = video_connections.find('preferredMacAddress').text
            v.preferred_interface_address = video_connections.find('preferredInterfaceAddress').text
            v.path = path
            v.buffer_time = video_connections.find('buffer').text
            v.network_timeout = video_connections.find('timeout').text
            v.rtsp_reliable = video_connections.find('rtspReliable').text
            path_config = MediaMTXPathConfig(None).serialize()
            path_config['sourceOnDemand'] = False
            v.mediamtx_settings = json.dumps(path_config)

            # Discard username and password for security
            feed = soup.find('feed')
            address = feed.find('address').text
            feed.find('address').string.replace_with(address.split("@")[-1])

            v.xml = str(feed)

            with app.app_context():
                try:
                    db.session.add(v)
                    db.session.commit()
                    logger.debug("Inserted Video")
                except sqlalchemy.exc.IntegrityError as e:
                    db.session.rollback()
                    v = db.session.execute(db.select(VideoStream).filter_by(path=v.path)).scalar_one()
                    v.protocol = video_connections.find('protocol').text
                    v.alias = video_connections.find('alias').text
                    v.uid = video_connections.find('uid').text
                    v.port = video_connections.find('port').text
                    v.rover_port = video_connections.find('roverPort').text
                    v.ignore_embedded_klv = (video_connections.find('ignoreEmbeddedKLV').text.lower() == 'true')
                    v.preferred_mac_address = video_connections.find('preferredMacAddress').text
                    v.preferred_interface_address = video_connections.find('preferredInterfaceAddress').text
                    v.path = video_connections.find('path').text
                    v.buffer_time = video_connections.find('buffer').text
                    v.network_timeout = video_connections.find('timeout').text
                    v.rtsp_reliable = video_connections.find('rtspReliable').text
                    feed = soup.find('feed')
                    address = feed.find('address').text
                    feed.find('address').replace_with(address.split("@")[-1])

                    v.xml = str(feed)

                    db.session.commit()
                    logger.debug("Updated video")

        return '', 200

    elif request.method == 'GET':
        try:
            with app.app_context():
                videos = db.session.execute(db.select(VideoStream)).scalars()
                videoconnections = Element('videoConnections')

                for video in videos:
                    # Make sure videos have the correct address based off of the Flask request and not 127.0.0.1
                    # This also forces all streams to bounce through MediaMTX
                    feed = BeautifulSoup(video.xml, 'xml')

                    url = urlparse(request.url_root).hostname
                    path = feed.find('path').text
                    if not path.startswith("/"):
                        path = "/" + path

                    if 'iTAK' in request.user_agent.string:
                        url = feed.find('protocol').text + "://" + url + ":" + feed.find("port").text + path

                    if feed.find('address'):
                        feed.find('address').string.replace_with(url)
                    else:
                        address = feed.new_tag('address')
                        address.string = url
                        feed.feed.append(address)
                    videoconnections.append(fromstring(str(feed)))

            return tostring(videoconnections), 200
        except BaseException as e:
            logger.error(traceback.format_exc())
            return '', 500
