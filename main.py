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
    "フォーマット以外の文章は一切出力しないでください。\n\n"
    "【拠点の判定ルール】\n"
    "・供給地点特定番号（22桁）が異なるごとに1つのマッチング情報を作成する。\n"
    "・同一住所でも電灯・動力など契約が複数あり供給地点特定番号が異なる場合は②③と増やす。\n"
    "・供給地点特定番号が同じ書類（申込書と明細書など）は1つのマッチング情報に統合する。\n\n"
    "【出力ルール】\n"
    "・案件名は冒頭に1回だけ出力する。\n"
    "・（登録契約情報）は1回だけ出力する。複数拠点でも繰り返さない。\n"
    "・マッチング情報は拠点ごとに①②③...と番号を付けて繰り返す。\n\n"
    "【抽出ルール】\n"
    "・案件名：申込兼同意書の「屋号」または「法人名」を入れる。\n"
    "・生年月日：YYYY年M月D日の形式で記載する（例：1952年7月11日）。昭和・平成・令和は西暦に変換する。\n"
    "・代表者名：姓と名の間に全角スペースを入れる。\n"
    "・カナ：スペース・空白は一切入れない。\n"
    "・住所：都道府県名から記載する。\n"
    "・地番：電気代明細書に印字されている「供給地点特定番号」を最優先で使用する。明細書がない場合のみ申込書の番号を使用する。必ず左から右へ1桁ずつ丁寧に読み取り、22桁の数字のみを記載する。読み取り後に再度確認して正確に記載すること。住所は絶対に入れない。\n"
    "・名義：申込書・お申し込み内容の詳細に「名義」と明記されている欄を最優先で使用する。申込確認書の「ご契約者名」「ご契約者」欄は名義に使わない。「名義」欄がない場合のみ、請求書の「ご契約名義」欄を使用する。姓と名の間に全角スペースを入れる。カナは入れない。\n"
    "・客番：申込確認書・申込書の「お客様番号」「契約番号」欄を優先して抽出する。\n"
    "・容量：数字とアルファベットのみ（例：60A、6KW）。B・低圧・電灯・動力などは除く。\n"
    "・電力会社：申込書に記載の新しい電力会社名を簡潔に記載する（例：ハルエネ、東京電力、エネパル）。\n"
    "・適用月：電気代明細書の検針日をYYYY/M/D形式で記載する（例：2025/9/3）。検針日がない場合は利用期間の終了日の翌日。\n"
    "・使用量：数字のみ記載する。kWhなどの単位は除く。\n"
    "・該当する情報が見つからない場合はその項目を空白のままにする。\n\n"
    "案件名：\n\n"
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
    lines = text.split('\n')
    case_name = ''
    main_lines = []

    for line in lines:
        if line.startswith('案件名：'):
            case_name = line
        else:
            main_lines.append(line)

    main_text = '\n'.join(main_lines).strip()
    second_text = case_name + '\n登録用エビデンス' if case_name else ''

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
    verification = (
        "【重要】出力する前に、地番（供給地点特定番号）を画像と照合して再確認してください。"
        "特に数字の順番が正しいか、桁数が22桁かを必ず確認してから出力してください。"
    )
    content.append({'type': 'text', 'text': PROMPT + '\n\n' + verification})
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
