import os
import hmac
import hashlib
import base64
import json
import threading
import requests
from flask import Flask, request, abort
import anthropic

app = Flask(__name__)

CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
CHANNEL_TOKEN  = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']

user_images = {}

PROMPT = (
    "複数の画像（申込書・電気代請求書・申込確認書・明細画面など）を総合して情報を抽出してください。\n"
    "フォーマット以外の文章は一切出力しないでください。\n\n"
    "【処理手順】\n"
    "ステップ1：申込書・明細書・請求書・画面スクリーンショットなど、すべての画像を1枚ずつ丁寧に確認する。\n"
    "ステップ2：各画像の「供給地点特定番号」「供給地点指定番号」と記載されている箇所を探し、22桁の番号をすべて書き出す。申込書だけでなく明細書・請求書ページにある番号も必ず含める。\n"
    "ステップ3：書き出した番号のうちユニークな番号の数だけマッチング情報を作成する。\n"
    "ステップ4：契約サマリー画面（ご契約者名・プラン名・容量が載っている画面）と明細書を容量・プラン名で照合し、各拠点の名義を特定する。\n"
    "ステップ5：各マッチング情報に対応する明細書の使用量・検針日・容量・客番などを、供給地点特定番号で照合して該当画像から紐付けて記載する。必ず各拠点の番号と一致する明細書から情報を取ること。\n"
    "ステップ6：フォーマット通りに出力する。\n\n"
    "【拠点の判定ルール】\n"
    "・供給地点特定番号が異なるごとに1つのマッチング情報を作成する。\n"
    "・同一住所でも電灯・動力など契約が複数あり番号が異なる場合は②③と増やす。\n"
    "・番号が同じ書類（申込書と明細書など）は1つのマッチング情報に統合する。\n\n"
    "【出力ルール】\n"
    "・案件名は冒頭に1回だけ出力する。\n"
    "・（登録契約情報）は1回だけ出力する。複数拠点でも繰り返さない。\n"
    "・マッチング情報は拠点ごとに①②③...と番号を付けて繰り返す。\n\n"
    "【抽出ルール】\n"
    "・案件名：申込兼同意書の「屋号」または「法人名」を入れる。\n"
    "・生年月日：YYYY年M月D日の形式で記載する（例：1952年7月11日）。元号は西暦に変換する。変換式：昭和N年＝(1925＋N)年、平成N年＝(1988＋N)年、令和N年＝(2018＋N)年。手書きの年号の数字は1桁ずつ丁寧に読み取り、変換してから出力する（例：昭和28年→1925＋28＝1953年）。申込兼同意書の生年月日欄に年が2桁のみ記載されている場合（例：「28年6月2日」）は和暦として扱い、フォームの元号チェックボックスや「昭和・平成・令和」の表記を確認して西暦に変換する。元号の表記が一切なく2桁のみの場合は昭和として変換する。\n"
    "・代表者名：姓と名の間に全角スペースを入れる。\n"
    "・（登録契約情報）のカナ：姓と名の間に全角スペースを1つ入れる。\n"
    "・（マッチング情報）のカナ：スペース・空白は一切入れない。\n"
    "・会社名：申込兼同意書の「屋号」または「法人名」を入れる（案件名と同じ値）。\n"
    "・住所：都道府県名から記載する。\n"
    "・地番：電気代明細書に印字されている供給地点特定番号を最優先で使用する。明細書がない場合は申込書の番号を使用する。必ず左から右へ1桁ずつ丁寧に読み取り、22桁の数字のみを記載する。住所は絶対に入れない。\n"
    "・名義：以下の優先順位で抽出する。\n"
    "  1. 申込書・お申し込み内容の詳細に「名義」と明記されている欄（最優先）。\n"
    "  2. 各拠点に対応する契約サマリー画面の「ご契約者名」欄。契約プランの容量（例：40A・30A・4kW）で明細書と紐付け、拠点ごとに正しい名義を特定する。\n"
    "  3. 上記がない場合のみ、請求書の「ご契約名義」欄を使用する。\n"
    "  ※申込確認書（エネパル等）のご契約者名は法人名込みの場合があるため使わない。\n"
    "  ※姓と名の間に全角スペースを入れる。\n"
    "・客番：申込兼同意書右側の「現在の電力会社のお客様番号」欄を最優先で使用する。容量が「A」または「KVA」の拠点（電灯契約）は「電灯」欄のお客様番号を、容量が「KW」の拠点（動力契約）は「動力」欄のお客様番号を使用する。申込兼同意書に記載がない場合は、申込確認書・申込書の「お客様番号」「契約番号」欄を使用する。1桁ずつ正確に読み取ること。\n"
    "・容量：数字とアルファベットのみ（例：60A、6KW）。B・低圧・電灯・動力などは除く。\n"
    "・電力会社：申込兼同意書右側の「現在の電力会社」欄を最優先で使用する。容量が「A」または「KVA」の拠点（電灯契約）は「現在の電力会社」の「電灯」欄の値を、容量が「KW」の拠点（動力契約）は「現在の電力会社」の「動力」欄の値を使用する。申込兼同意書に「現在の電力会社」の記載がない場合のみ、電気代明細書または申込確認書に記載されている電力会社名を使用する。その際、以下の読み替えを行う：明細書の発行者が「つくば電気プラン」の場合は「地域創生」、「株式会社フォーバルテレコム」または「フォーバルテレコム」の場合は「フォーバル」、「株式会社エネバル」の場合は「エネバル」と記載する。申込兼同意書のヘッダーや申込先会社名（U-POWERなど）は使わない。\n"
    "・適用月：各マッチング情報の地番（供給地点特定番号）と同じ番号が記載されている明細書を特定し、その明細書の「検針日」または「検針月日」をYYYY年M月D日形式で記載する（「検針日」「検針月日」どちらの表記でも同様に扱う）。日付がYYYY-MM-DD形式の場合も正しく読み取ること。検針日・検針月日の記載がない場合は利用期間の終了日を使用する。拠点①の適用月は拠点①の供給地点特定番号が載っている明細書から、拠点②の適用月は拠点②の明細書から読み取ること。異なる拠点の明細書の日付を絶対に混同しないこと。\n"
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
        "【出力前の最終確認】"
        "1. すべての画像を再スキャンし、見落とした供給地点特定番号がないか確認する。"
        "2. 各地番が正確に22桁の数字になっているか確認する。"
        "3. マッチング情報の数がユニークな供給地点特定番号の数と一致しているか確認する。"
        "これらを確認してから出力してください。"
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


def process_completion(user_id, reply_token, images):
    reply(reply_token, '解析を開始します。少々お待ちください…')
    try:
        raw = extract_info(images)
        main_text, second_text = parse_result(raw)
        push(user_id, main_text)
        if second_text:
            push(user_id, second_text)
    except Exception as e:
        push(user_id, 'エラーが発生しました。\n(' + str(e) + ')')


@app.route('/health', methods=['GET'])
def health():
    return 'OK'


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
                threading.Thread(
                    target=process_completion,
                    args=(user_id, reply_token, images),
                    daemon=True
                ).start()

    return 'OK'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
