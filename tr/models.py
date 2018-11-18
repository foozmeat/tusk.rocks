import pprint as pp
import re
from datetime import datetime, timedelta

import requests
from flask import render_template
from metadata_parser import MetadataParser
from requests import Request
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
    title = Column(String(100), nullable=True)
    album_art = Column(String(200), nullable=True)

    share_link = Column(String(400), nullable=False)
    posted = Column(Boolean, nullable=False, default=False)
    toot_visibility = Column(String(40), nullable=True)
    status_id = Column(BigInteger, default=0)

    created = Column(DateTime, default=datetime.utcnow)
    updated = Column(DateTime)

    md = None

    @property
    def share_link_is_song_link(self):
        pattern = re.compile("^https://song.link/")
        if pattern.search(self.share_link):
            return True
        else:
            return False

    @property
    def share_link_is_bandcamp(self):
        pattern = re.compile("^https://.*bandcamp.com/")
        if pattern.search(self.share_link):
            return True
        else:
            return False

    @property
    def share_link_is_soundcloud(self):
        pattern = re.compile("^https://soundcloud.com/")
        if pattern.search(self.share_link):
            return True
        else:
            return False

    @property
    def song_link(self):
        if self.share_link_is_song_link:
            return self.share_link
        elif self.share_link_is_bandcamp:
            return self.share_link
        elif self.share_link_is_soundcloud:
            return self.share_link
        else:
            return f"https://song.link/{self.share_link}"

    def fetch_metadata(self) -> None:

        if self.album_art or self.title:
            return

        if not self.md:
            req = Request('GET', self.song_link, headers={'User-Agent': 'curl/7.54.0'})
            prepped = req.prepare()
            s = requests.Session()
            r = s.send(prepped)

            if r.status_code == 200:
                self.share_link = r.url

                mp = MetadataParser(html=r.text, search_head_only=True)
                self.md = mp.metadata
                self.title = self.md['og']['title']
                self.album_art = self.md['og']['image']

    @property
    def post_link(self):
        if self.status_id:
            output = f"{self.user.profile_link}/{self.status_id}"
            return output
        else:
            return None

    def preview_content(self):
        self.fetch_metadata()
        p_text = render_template('_post_preview.html.j2',
                                 link=self.song_link,
                                 title=self.title,
                                 thumbnail_url=self.album_art
                                 )

        return p_text


class User(Base):
    __tablename__ = 'users'
    __table_args__ = {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_general_ci'}

    id = Column(Integer, primary_key=True)

    mastodon_access_code = Column(String(80), nullable=False)
    mastodon_account_id = Column(BigInteger, default=0)
    mastodon_user = Column(String(30), nullable=False)
    mastodon_host_id = Column(Integer, ForeignKey('mastodon_host.id'), nullable=False)

    settings_id = Column(Integer, ForeignKey('settings.id'), nullable=True)
    posts = relationship("Post", backref="user")

    created = Column(DateTime, default=datetime.utcnow)
    updated = Column(DateTime)

    @property
    def profile_link(self):
        url = f"https://{self.mastodon_host.hostname}/@{self.mastodon_user}"
        return url
