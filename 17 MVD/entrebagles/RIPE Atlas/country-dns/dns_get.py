#! /usr/bin/env python3

from flask import Flask, request, jsonify
from get_ns_country import get_ns_list

app = Flask(__name__)


@app.route('/')
def index():
    return "Please use: /dns_get?country=CC !"


@app.route('/dns_get', methods=['GET'])
def dns_get():
    cc = request.args.get('country', '')
    servers = get_ns_list(cc)
    return servers


if __name__ == '__main__':
    app.run(debug=True)
