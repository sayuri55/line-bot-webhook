from flask import Flask, request, abort
import hashlib, hmac, base64, json, os, requests

app = Flask(__name__)

CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']

@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    hash = hmac.new(CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    if base64.b64encode(hash).decode() != signature:
        abort(400)
    events = json.loads(body).get('events', [])
    for event in events:
        if event.get('type') == 'message' and event['message']['type'] == 'text':
            reply_token = event['replyToken']
            text = event['message']['text']
            requests.post('https://api.line.me/v2/bot/message/reply', json={
                'replyToken': reply_token,
                'messages': [{'type': 'text', 'text': text}]
            }, headers={'Authorization': f'Bearer {ACCESS_TOKEN}'})
    return 'OK'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

「Commit changes」をクリック。

---
2. requirements.txt を書き直す

flask
requests
gunicorn
