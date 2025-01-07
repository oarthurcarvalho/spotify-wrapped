import base64
import configparser
import hashlib
import json
import secrets
from datetime import datetime
from time import sleep

import boto3
import requests


class SpotifyScraper:

    def __init__(self) -> None:

        self.config = configparser.ConfigParser()
        self.config.read('.env')
        self.client_id = self.config['SPOTIFY']['client_id']
        self.redirect_uri = 'http://localhost:3000'

    def _update_token(self, access_token, refresh_token):

        if 'SPOTIFY' not in self.config:
            self.config['SPOTIFY'] = {}

        self.config['SPOTIFY']['access_token'] = access_token
        self.config['SPOTIFY']['refresh_token'] = refresh_token
        self.config['SPOTIFY']['last_date_token'] = datetime.now().strftime(
            '%d/%m/%Y %H:%M:%S')

        with open('.env', 'w') as f:
            self.config.write(f)

    def _new_login(self):

        code_verifier = base64.urlsafe_b64encode(
            hashlib.sha256(secrets.token_bytes(32)).digest()
        ).decode('utf-8').rstrip('=')

        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode('utf-8').rstrip('=')

        auth_url = 'https://accounts.spotify.com/authorize'
        scope = 'user-read-recently-played'

        auth_params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': scope,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }

        print("Open this URL in your browser to authorize the application:")
        print(f"{auth_url}?{requests.compat.urlencode(auth_params)}")
        auth_code = input("Paste the authorization code here: ")

        token_url = 'https://accounts.spotify.com/api/token'
        data = {
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.redirect_uri,
            'client_id': self.client_id,
            'code_verifier': code_verifier
        }

        response = requests.post(token_url, data=data)
        if response.status_code != 200:
            raise Exception("Failed to get access token.")

        access_token = response.json()['access_token']
        refresh_token = response.json().get('refresh_token', None)

        self._update_token(access_token, refresh_token)
        return access_token

    def get_access_token(self):

        access_token = self.renew_access_token()

        if not access_token:
            access_token = self._new_login()

        return access_token

    def renew_access_token(self):

        token_url = "https://accounts.spotify.com/api/token"
        refresh_token = self.config['SPOTIFY']['refresh_token']

        client_id = self.config['SPOTIFY']['client_id']
        client_secret = self.config['SPOTIFY']['client_secret']
        client_creds = f"{client_id}:{client_secret}"
        client_creds_b64 = base64.b64encode(client_creds.encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {client_creds_b64}"
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }

        response = requests.post(token_url, headers=headers, data=data)

        if response.status_code == 200:

            access_token = response.json()['access_token']
            new_refresh_token = response.json()['refresh_token']

            self._update_token(access_token, new_refresh_token)

            return access_token

        return None

    def _verify_valid_token(self):

        last_date_token = datetime.strptime(
            self.config['SPOTIFY']['last_date_token'],
            "%d/%m/%Y %H:%M:%S"
        )
        now_date = datetime.now()

        time_active_token_in_minutes = round(
            (
                now_date - last_date_token
            ).total_seconds() / 60
        )

        if time_active_token_in_minutes > 60:
            return False

        return True

    def get_tracks_history(self, unix_date):

        url = 'https://api.spotify.com/v1/me/player/recently-played'
        limit = 50
        before = unix_date

        if self._verify_valid_token():
            access_token = self.config['SPOTIFY']['access_token']
        else:
            access_token = self.get_access_token()

        params = {
            'limit': limit,
            'before': before
        }

        header = {
            'Authorization': f'Bearer {access_token}'
        }

        response = requests.get(url, params=params, headers=header)

        if len(response.json()['items']) == 0:
            return 0

        before = int(response.json()['cursors']['before'])

        data = response.json()['items']

        with open('resultado.json', 'w') as json_file:
            json.dump(data, json_file, indent=4)

        self.save_to_s3(data)

        return before

    def save_to_s3(self, data):

        s3_client = boto3.client(
            's3',
            aws_access_key_id=self.config['AWS']['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=self.config['AWS']['AWS_SECRET_ACCESS_KEY'],
            region_name=self.config['AWS']['AWS_REGION']
        )

        bucket_name = self.config['AWS']['S3_BUCKET_NAME']
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'tracks_history_{timestamp}.json'

        json_data = json.dumps(data, indent=4)

        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename,
            Body=json_data,
            ContentType='application/json'
        )

        print(f'Arquivo salvo no S3: {bucket_name}/{filename}')

    def get_data(self, data):

        album_data = data['track']['album']

        output_data = {
            'id_album': album_data['id'],
            'name_album': album_data['name'],
            'release_date': album_data['release_date'],
            'total_tracks': album_data['total_tracks'],
            'album_type': album_data['album_type'],
            'image_album': album_data['images'][1]['url'],
            'artist_album_id': album_data['artists'][0]['id'],
            'artist_album_name': album_data['artists'][0]['name'],
            'duration_ms': data['track']['duration_ms'],
            'explicit': data['track']['explicit'],
            'track_id': data['track']['id'],
            'is_local': data['track']['is_local'],
            'track_name': data['track']['name'],
            'popularity': data['track']['popularity'],
            'played_at': data['played_at'],
            'type': data['context']['type']
            if not data['context'] is None else 'null',
            'playlist_url': data['context']['external_urls']['spotify']
            if not data['context'] is None else 'null'
        }

        return output_data


def main():

    date_now = datetime.now()
    date_now = int(round(date_now.timestamp() * 1000))

    spotify_scraper = SpotifyScraper()

    next_page = date_now

    while next_page:
        next_page = spotify_scraper.get_tracks_history(next_page)
        sleep(2)


if __name__ == '__main__':

    main()
