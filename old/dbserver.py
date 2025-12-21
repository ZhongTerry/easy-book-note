from flask import Flask, request, jsonify, send_file
import subprocess
import os

app = Flask(__name__)

# 获取当前文件的目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def run_db_command(command, key="", value=""):
    try:
        args = ["./notedb.exe", command, key, value] if value else ["./notedb.exe", command, key]
        result = subprocess.run(args, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        return str(e)

@app.route('/')
def index():
    # 返回 index.html 文件
    return send_file(os.path.join(BASE_DIR, 'index.html'))

@app.route('/insert', methods=['POST'])
def insert():
    data = request.json
    key = data.get('key', '')
    value = data.get('value', '')
    result = run_db_command("insert", key, value)
    return jsonify({"result": result})

@app.route('/update', methods=['POST'])
def update():
    data = request.json
    key = data.get('key', '')
    value = data.get('value', '')
    result = run_db_command("update", key, value)
    return jsonify({"result": result})

@app.route('/remove', methods=['POST'])
def remove():
    data = request.json
    key = data.get('key', '')
    result = run_db_command("remove", key)
    return jsonify({"result": result})

@app.route('/find', methods=['POST'])
def find():
    data = request.json
    key = data.get('key', '')
    result = run_db_command("find", key)
    return jsonify({"result": result})

@app.route('/list', methods=['POST'])
def list_all():
    result = run_db_command("list")
    return jsonify({"result": result})

if __name__ == '__main__':
    app.run(debug=True)