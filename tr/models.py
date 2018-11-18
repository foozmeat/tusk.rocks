import math
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
                image_link = self.md['og']['image']

                if image_link[0:5] == 'http:':
                    image_link = 'https:' + image_link[5:]

                self.album_art = image_link

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

    @property
    def relative_date(self) -> str:
        return reltime(self.created)


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


def reltime(date, compare_to=None, at='@') -> str:
    """
    Modified From https://gist.githubusercontent.com/deontologician/3503910/raw/bf46f646d79bd6d3cb29fcf23be5a72a6a92c185/reltime.py
    """

    def ordinal(n):
        r"""Returns a string ordinal representation of a number
        Taken from: http://stackoverflow.com/a/739301/180718
        """
        if 10 <= n % 100 < 20:
            return str(n) + 'th'
        else:
            return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, "th")

    compare_to = compare_to or datetime.utcnow()
    if date > compare_to:
        raise NotImplementedError('reltime only handles dates in the past')
    # get timediff values
    diff = compare_to - date
    if diff.seconds < 60 * 60 * 8:  # less than a business day?
        days_ago = diff.days
    else:
        days_ago = diff.days + 1
    months_ago = compare_to.month - date.month
    years_ago = compare_to.year - date.year
    weeks_ago = int(math.ceil(days_ago / 7.0))
    # get a non-zero padded 12-hour hour
    hr = date.strftime('%I')
    if hr.startswith('0'):
        hr = hr[1:]
    wd = compare_to.weekday()
    # calculate the time string
    if date.minute == 0:
        time = '{0}{1}'.format(hr, date.strftime('%p').lower())
    else:
        time = '{0}:{1}'.format(hr, date.strftime('%M%p').lower())
    # calculate the date string
    if days_ago == 0:
        datestr = 'today'
    elif days_ago == 1:
        datestr = 'yesterday'
    elif days_ago > 6 and months_ago == 0:
        datestr = '{weeks_ago} weeks ago'
    # elif (wd in (5, 6) and days_ago in (wd + 1, wd + 2)) or \
    #         wd + 3 <= days_ago <= wd + 8:
    #     # this was determined by making a table of wd versus days_ago and
    #     # divining a relationship based on everyday speech. This is somewhat
    #     # subjective I guess!
    #     datestr = '{days_ago} days ago'
    elif days_ago <= wd + 2:
        datestr = '{days_ago} days ago'
    else:
        datestr = '{month} {day}, {year}'
    return datestr.format(time=time,
                          weekday=date.strftime('%A'),
                          day=ordinal(date.day),
                          days=diff.days,
                          days_ago=days_ago,
                          month=date.strftime('%B'),
                          years_ago=years_ago,
                          months_ago=months_ago,
                          weeks_ago=weeks_ago,
                          year=date.year,
                          at=at)
