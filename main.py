from flask import Flask, request, abort
import hashlib, hmac, base64, json, os, requests
import anthropic

app = Flask(__name__)

CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']

user_images = {}

def verify_signature(body, signature):
    hash = hmac.new(CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return base64.b64encode(hash).decode() == signature

def get_line_image(message_id):
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    res = requests.get(url, headers={'Authorization': f'Bearer {ACCESS_TOKEN}'})
    return res.content

def reply(reply_token, text):
    requests.post('https://api.line.me/v2/bot/message/reply', json={
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}]
    }, headers={'Authorization': f'Bearer {ACCESS_TOKEN}'})

def analyze_images(images_data):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content = []
    for img in images_data:
        content.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(img).decode()}
        })
    content.append({
        'type': 'text',
        'text': '''送られた書類の画像から情報を抽出して、以下のフォーマットで出力してください。
情報がない場合は空欄のままにしてください。

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
使用量：


②＝マッチング情報＝
・地番：
・住所：
・名義：
・カナ：
電力会社：
客番：
容量：
適用月：
使用量：

複数の電力会社の検針票がある場合は①②に分けて入力してください。
申込書がある場合はそこから登録契約情報を抽出してください。'''
    })
    message = client.messages.create(
        model='claude-opus-4-8',
        max_tokens=2000,
        messages=[{'role': 'user', 'content': content}]
    )
    return message.content[0].text

@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    if not verify_signature(body, signature):
        abort(400)
    events = json.loads(body).get('events', [])
    for event in events:
        user_id = event.get('source', {}).get('userId', '')
        reply_token = event.get('replyToken', '')
        if event.get('type') == 'message':
            msg = event.get('message', {})
            if msg.get('type') == 'image':
                img_data = get_line_image(msg['id'])
                if user_id not in user_images:
                    user_images[user_id] = []
                user_images[user_id].append(img_data)
                reply(reply_token, f'画像を受け取りました（{len(user_images[user_id])}枚）。\n全部送り終わったら「完了」と送ってください。')
            elif msg.get('type') == 'text':
                text = msg.get('text', '')
                if text == '完了' and user_id in user_images and user_images[user_id]:
                    reply(reply_token, '画像を解析中です。少々お待ちください...')
                    result = analyze_images(user_images[user_id])
                    user_images[user_id] = []
                    requests.post('https://api.line.me/v2/bot/message/push', json={
                        'to': user_id,
                        'messages': [{'type': 'text', 'text': result}]
                    }, headers={'Authorization': f'Bearer {ACCESS_TOKEN}'})
                else:
                    reply(reply_token, '書類の写真を送ってください。全部送り終わったら「完了」と送ってください。')
    return 'OK'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
