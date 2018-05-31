from flask import Flask, request
from flask_restful import Resource, Api
from sqlalchemy import create_engine
from json import dumps
from flask.ext.jsonpify import jsonify

app = Flask(__name__)
api = Api(app)

cc_map = {
'ar': [],
'uy':[],
'br':[]
}




import dns.resolver
# from flask import jsonify

def get_ns(CC):
    CC_absoluto = CC + '.'
    myResolver = dns.resolver.Resolver()
    myResolver.search = ''
    servers = myResolver.query(CC_absoluto, 'NS')
    return(servers)

def get_ns_list(CC):
    servers = get_ns(CC)
    srv_list = [str(i) for i in servers]
    return srv_list

def populate_map():
    for cc in cc_map.keys():
        ns_list = get_ns_list(cc)
        cc_map[cc] = ns_list

class Ccs(Resource):
    def get(self, cc):
        # print cc, cc_map
        ns_list = cc_map[cc]
        result = {'nameservers': ns_list}
        return jsonify(result)

api.add_resource(Ccs, '/cc/<string:cc>')


if __name__ == '__main__':
    populate_map()
    app.run(port='5002')
