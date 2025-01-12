from dataclasses import dataclass
from datetime import datetime

from opentakserver.extensions import db
from sqlalchemy import Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opentakserver.functions import iso8601_string_from_datetime


@dataclass
class EUD(db.Model):
    __tablename__ = "euds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    callsign: Mapped[str] = mapped_column(String, nullable=True)
    device: Mapped[str] = mapped_column(String, nullable=True)
    os: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=True)
    version: Mapped[str] = mapped_column(String, nullable=True)
    phone_number: Mapped[int] = mapped_column(Integer, nullable=True)
    last_event_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String, nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=True)
    team_role: Mapped[str] = mapped_column(String, nullable=True)
    points = relationship("Point", back_populates="eud")
    cots = relationship("CoT", back_populates="eud")
    casevacs = relationship("CasEvac", back_populates="eud")
    geochats = relationship("GeoChat", back_populates="eud")
    chatroom_uid = relationship("ChatroomsUids", back_populates="eud")
    user: Mapped["User"] = relationship(back_populates="euds")
    alert = relationship("Alert", back_populates="eud")
    data_packages = relationship("DataPackage", back_populates="eud", uselist=False)
    certificate = relationship("Certificate", back_populates="eud", uselist=False)
    markers = relationship("Marker", back_populates="eud")
    rb_lines = relationship("RBLine", back_populates="eud")
    team = relationship("Team", back_populates="euds")

    def serialize(self):
        return {
            'uid': self.uid,
            'callsign': self.callsign,
            'device': self.device,
            'os': self.os,
            'platform': self.platform,
            'version': self.version,
            'phone_number': self.phone_number,
            'last_event_time': self.last_event_time,
            'last_status': self.last_status,
            'user_id': self.user_id,
            'team_id': self.team_id,
            'team_role': self.team_role
        }

    def to_json(self, include_data_packages=True):
        config_datapackage_hash = None
        if self.certificate and self.certificate.data_package:
            config_datapackage_hash = self.certificate.data_package.hash
        return {
            'uid': self.uid,
            'callsign': self.callsign,
            'device': self.device,
            'os': self.os,
            'platform': self.platform,
            'version': self.version,
            'phone_number': self.phone_number,
            'last_event_time': iso8601_string_from_datetime(self.last_event_time) if self.last_event_time else None,
            'last_status': self.last_status,
            'username': self.user.username if self.user else None,
            'last_point': self.points[-1].to_json() if self.points else None,
            'team': self.team.name if self.team else None,
            'team_color': self.team.get_team_color() if self.team else None,
            'team_role': self.team_role,
            'data_packages': self.data_packages.to_json(False) if include_data_packages and self.data_packages else None,
            'config_hash': config_datapackage_hash
        }
