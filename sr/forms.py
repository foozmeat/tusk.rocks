from flask_wtf import FlaskForm
from wtforms import BooleanField, RadioField, StringField
from wtforms.validators import DataRequired, Email, Length


class MastodonIDForm(FlaskForm):
    mastodon_id = StringField('Enter your Mastodon ID', validators=[DataRequired(), Email()])
