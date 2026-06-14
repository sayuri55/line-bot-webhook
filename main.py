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
    "複数の画像（申込書・電気代請求書・申込確認書など）を総合して情報を抽出してください。\n"
    "【重要】複数の拠点・案件の書類が混在している場合は、案件ごとにフォーマットを繰り返して出力してください。\n"
    "案件と案件の間は「========」で区切ってください。\n"
    "フォーマット以外の文章は一切出力しないでください。\n\n"
    "【抽出ルール】\n"
    "・案件名：申込兼同意書の「屋号」または「法人名」を入れる。\n"
    "・地番：供給地点特定番号を入れる。必ず22桁の数字のみ。住所は絶対に入れない。\n"
    "・住所：番地の数字とハイフンはすべて全角にする（例：4240-6は４２４０－６）。\n"
    "・名義：申込確認書・請求書の「名義」「ご契約名義」「ご契約者」欄を最優先で使用する。申込書の「名称」欄は名義に使わない。スペースは全角スペースで入れる。\n"
    "・カナ：スペース・空白は一切入れない。\n"
    "・容量：数字とアルファベットのみ（例：60A、6KW）。B・低圧・電灯・動力などの文字はすべて除く。\n"
    "・電力会社：「ハルエネ」「東京電力」「地域創生」のように簡潔な名称で記載する。\n"
    "・客番：「お客様番号」「契約番号」欄を優先して抽出する。\n"
    "・適用月：検針日の年月日（例：2026年6月13日）。検針日がない場合は利用期間の終了日の翌月の同日。\n"
    "・該当する情報が見つからない場合はその項目を空白のままにする。\n\n"
    "案件名：\n"
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


def verify_signature(body, signature):
    digest = hmac.new(
        CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256
    ).digest()
    return signature == base64.b64encode(digest).decode('utf-8')


def get_line_image(message_id):
    res = requests.get(
        'https://api-data.line.me/v2/bot/message/' + message_id + '/content',
        headers={'Authorization': 'Bearer ' + CHANNEL_TOKEN},
        timeout=15
    )
    res.raise_for_status()
    return res.content


def to_fullwidth_address(text):
    table = str.maketrans('0123456789-', '０１２３４５６７８９－')
    return text.translate(table)


def postprocess(text):
    lines = text.split('\n')
    result = []
    for line in lines:
        if '住所：' in line:
            idx = line.index('住所：') + 3
            result.append(line[:idx] + to_fullwidth_address(line[idx:]))
        else:
            result.append(line)
    return '\n'.join(result)


def parse_result(text):
    cases = [c.strip() for c in text.split('========') if c.strip()]
    main_parts = []
    case_names = []

    for case in cases:
        lines = case.split('\n')
        name_line = ''
        other_lines = []
        for line in lines:
            if line.startswith('案件名：'):
                name_line = line
            else:
                other_lines.append(line)
        if name_line:
            case_names.append(name_line)
        main_parts.append('\n'.join(other_lines).strip())

    main_text = '\n\n========\n\n'.join(main_parts)

    second_parts = []
    for name in case_names:
        second_parts.append(name + '\n登録用エビデンス')
    second_text = '\n\n========\n\n'.join(second_parts)

    return main_text, second_text


def extract_info(images_list):
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
    return postprocess(message.content[0].text)


def reply(reply_token, text):
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + CHANNEL_TOKEN
        },
        json={
            'replyToken': reply_token,
            'messages': [{'type': 'text', 'text': text}]
        },
        timeout=10
    )


def push(user_id, text):
    requests.post(
        'https://api.line.me/v2/bot/message/push',
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + CHANNEL_TOKEN
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

        elif msg.get('type') == 'text' and msg.get('text', '').strip() == '完了':
            images = user_images.pop(user_id, [])
            if not images:
                reply(reply_token, '画像が見つかりません。先に画像を送ってください。')
            else:
                reply(reply_token, '解析を開始します。少々お待ちください…')
                try:
                    raw = extract_info(images)
                    main_text, second_text = parse_result(raw)
                    push(user_id, main_text)
                    if second_text:
                        push(user_id, second_text)
                except Exception as e:
                    push(user_id, 'エラーが発生しました。\n(' + str(e) + ')')

    return 'OK'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
