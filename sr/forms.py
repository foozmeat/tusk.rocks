from flask_wtf import FlaskForm
from wtforms import BooleanField, RadioField, StringField, TextAreaField
from wtforms.fields.html5 import URLField
from wtforms.validators import DataRequired, Email, Length, optional, length


class MastodonIDForm(FlaskForm):
    mastodon_id = StringField('Enter your Mastodon ID', validators=[DataRequired(), Email()])


class SubmissionForm(FlaskForm):
    comment = TextAreaField('Comment', [length(max=500)])
    song_link = URLField('Link: ')
