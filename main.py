import os
import hmac
import hashlib
import base64
import json
import requests
from flask import Flask, request, abort
import anthropic

app = Flask(__name__)

CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
CHANNEL_TOKEN  = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']

user_images = {}

PROMPT = (
    "複数の画像（申込書・電気代請求書など）を総合して情報を抽出してください。\n"
    "【重要】複数の拠点・案件の書類が混在している場合は、案件ごとにフォーマットを繰り返して出力してください。\n"
    "案件と案件の間は「========」で区切ってください。\n"
    "フォーマット以外の文章は一切出力しないでください。\n\n"
    "【抽出ルール】\n"
    "・地番：供給地点特定番号（22桁の数字のみ）を入れる。住所は絶対に入れない。\n"
    "・名義：請求書・お客様情報の「名義」「ご契約名義」欄を優先して抽出する。スペースは全角スペースで入れる。漢字を正確に読み取ること。\n"
    "・客番：「お客様番号」「契約番号」と記載されている欄を優先して抽出する。見つからない場合は他の番号欄から探す。\n"
    "・カナ：スペース・空白は一切入れない。\n"
    "・容量：数字とアルファベットのみ（例：60A、6KW）。B・低圧・電灯・動力・契約種別などの文字はすべて除く。\n"
    "・適用月：検針日の年月日（例：2026年6月13日）。検針日がない場合は利用期間の終了日の翌月の同日。\n"
    "・該当する情報が見つからない場合はその項目を空白のままにする。\n\n"
    "（登録契約情報）\n"
    "・生年月日：\n"
    "・代表者名：\n"
    "・カナ：\n"
    "・会社名：\n"
    "・郵便番号：\n"
    "・住所：\n"
    "・電話番号：\n\n\n"
    "①＝マッチング情報＝\n"
    "・地番：\n"
    "・住所：\n"
    "・名義：\n"
    "・カナ：\n"
    "電力会社：\n"
    "客番：\n"
    "容量：\n"
    "適用月：\n"
    "使用量："
)


def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(
        CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256
    ).digest()
    return signature == base64.b64encode(digest).decode('utf-8')


def get_line_image(message_id: str) -> bytes:
    res = requests.get(
        f'https://api-data.line.me/v2/bot/message/{message_id}/content',
        headers={'Authorization': f'Bearer {CHANNEL_TOKEN}'},
        timeout=15
    )
    res.raise_for_status()
    return res.content


def extract_info(images_list: list) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content = []
    for img in images_list:
        content.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': 'image/jpeg',
                'data': base64.b64encode(img).decode()
            }
        })
    content.append({'type': 'text', 'text': PROMPT})
    message = client.messages.create(
        model='claude-opus-4-8',
        max_tokens=4000,
        messages=[{'role': 'user', 'content': content}]
    )
    return message.content[0].text


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


def push(user_id: str, text: str):
    requests.post(
        'https://api.line.me/v2/bot/message/push',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {CHANNEL_TOKEN}'
        },
        json={
            'to': user_id,
            'messages': [{'type': 'text', 'text': text}]
        },
        timeout=60
    )


@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data()
    signature = request.headers.get('X-Line-Signature', '')

    if not verify_signature(raw_body, signature):
        abort(400)

    for event in json.loads(raw_body).get('events', []):
        user_id = event.get('source', {}).get('userId', '')
        reply_token = event.get('replyToken', '')
        msg = event.get('message', {})

        if event.get('type') != 'message':
            continue

        if msg.get('type') == 'image':
            image = get_line_image(msg['id'])
            if user_id not in user_images:
                user_images[user_id] = []
            user_images[user_id].append(image)
            count = len(user_images[user_id])
            reply(reply_token, f'画像を受け取りました（{count}枚）。\nすべて送り終わったら「完了」と送ってください。')

        elif msg.get('type') == 'text' and msg.get('text', '').strip() == '完了':
            images = user_images.pop(user_id, [])
            if not images:
                reply(reply_token, '画像が見つかりません。先に画像を送ってください。')
            else:
                reply(reply_token, f'解析を開始します（{len(images)}枚）。少々お待ちください...')
                try:
                    result = extract_info(images)
                    push(user_id, result)
                except Exception as e:
                    push(user_id, f'エラーが発生しました。\n({e})')

    return 'OK'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
