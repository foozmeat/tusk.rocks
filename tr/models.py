from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

metadata = MetaData()
Base = declarative_base(metadata=metadata)

PENALTY_TIME = 600  # 10 minutes


class Settings(Base):
    __tablename__ = 'settings'
    __table_args__ = {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_general_ci'}

    id = Column(Integer, primary_key=True)
    user = relationship('User', backref='settings', lazy='dynamic')


class MastodonHost(Base):
    __tablename__ = 'mastodon_host'
    __table_args__ = {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_general_ci'}

    id = Column(Integer, primary_key=True)
    hostname = Column(String(80), nullable=False)
    client_id = Column(String(64), nullable=False)
    client_secret = Column(String(64), nullable=False)
    created = Column(DateTime, default=datetime.utcnow)
    users = relationship('User', backref='mastodon_host', lazy='dynamic')
    defer_until = Column(DateTime)

    def defer(self):
        self.defer_until = datetime.now() + timedelta(seconds=PENALTY_TIME)


class Post(Base):
    __tablename__ = 'posts'
    __table_args__ = {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_general_ci'}
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))

    comment = Column(String(500), nullable=False)
    share_link = Column(String(400), nullable=False)
    posted = Column(Boolean, nullable=False, default=False)
    post_link = Column(String(400), nullable=False)

    created = Column(DateTime, default=datetime.utcnow)
    updated = Column(DateTime)


class User(Base):
    __tablename__ = 'users'
    __table_args__ = {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_general_ci'}

    id = Column(Integer, primary_key=True)

    mastodon_access_code = Column(String(80), nullable=False)
    mastodon_account_id = Column(BigInteger, default=0)
    mastodon_user = Column(String(30), nullable=False)
    mastodon_host_id = Column(Integer, ForeignKey('mastodon_host.id'), nullable=False)

    settings_id = Column(Integer, ForeignKey('settings.id'), nullable=True)
    posts = relationship("Post")

    created = Column(DateTime, default=datetime.utcnow)
    updated = Column(DateTime)


