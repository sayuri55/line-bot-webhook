import os
import hmac
import hashlib
import base64
import json
import requests
from flask import Flask, request, abort
import google.generativeai as genai

app = Flask(__name__)

CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
CHANNEL_TOKEN  = os.environ['LINE_CHANNEL_ACCESS_TOKEN']

genai.configure(api_key=os.environ['GEMINI_API_KEY'])

PROMPT = """この画像から情報を抽出して、以下のフォーマッ 。
フォーマット以外の文章は一切出力しないでください。

【抽出ルール】
・地番：画像中の「供給地点特定番号」（22桁の数字）を入れる。住所は入れない。
・名義：氏名のスペースは全角スペース（　）で入れる。
・カナ：スペース・空白は一切入れない。
・容量：数字とアルファベットのみ記載する（例：60A、6KW） 除く。
・適用月：画像の「検針日」の年月日を入れる（例：2026年6月13日）。検針日の記載がない場合は、利用期間の終了日の翌月の同日を入れる（例：終了日が2026年5月25日なら2026年6月25日）。
・該当する情報が見つからない場合はその項目を空白のままに

（登録契約情報）
・生年月日：
・代表者名：
・カナ：
・会社名：
・郵便番号：
・住所：
・電話番号：


①＝マッチング情報＝
・地番：
・住所：
・名義：
・カナ：
電力会社：
客番：
容量：
適用月：
使用量："""


def verify_signature(body: bytes, signature: str) -> bool
    digest = hmac.new(
        CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256
    ).digest()
    return signature == base64.b64encode(digest).decode('


def get_line_image(message_id: str) -> bytes:
    res = requests.get(
        f'https://api-data.line.me/v2/bot/message/{messag
        headers={'Authorization': f'Bearer {CHANNEL_TOKEN
        timeout=15
    )
    res.raise_for_status()
    return res.content


def extract_info(image_bytes: bytes) -> str:
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content([
        {'mime_type': 'image/jpeg', 'data': image_bytes},
        PROMPT
    ])
    return response.text


def reply(reply_token: str, text: str):
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {CHANNEL_TOKEN}'
        },
        json={
            'replyToken': reply_token,
            'messages': [{'type': 'text', 'text': text}]
        },
        timeout=10
    )


@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data()
    signature = request.headers.get('X-Line-Signature', '

    if not verify_signature(raw_body, signature):
        abort(400)

    for event in json.loads(raw_body).get('events', []):
        if event.get('type') == 'message' and event['mess
            try:
                image = get_line_image(event['message']['id'])
                result = extract_info(image)
                reply(event['replyToken'], result)
            except Exception as e:
                reply(event['replyToken'], f'エラーが発生 い。\n({e})')

    return 'OK'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
