#! /usr/bin/env python3

import argparse
import dns.resolver
from flask import jsonify


def get_ns(CC):
    CC_absoluto = CC + '.'
    myResolver = dns.resolver.Resolver()
    myResolver.search = ''
    servers = myResolver.query(CC_absoluto, 'NS')
    return (servers)


def get_ns_list(CC):
    servers = get_ns(CC)
    srv_list = [str(i) for i in servers]
    print (servers)
    print (srv_list)
    # return CC
    return srv_list


def parse_args():
    parser = argparse.ArgumentParser(
        description='Obtener los NS de un ccTLD de LAC')
    parser.add_argument('--country', type=str, required=True,
                        help='pais en la forma XX (country code)')
    return parser.parse_args()


if __name__ == "__main__":
    import sys

    args = parse_args()
    if args.country is not None:
        print("obtenemos los NS de ", args.country)
        servers = get_ns_list(args.country)

        print(servers)

    else:
        print("Country required")
