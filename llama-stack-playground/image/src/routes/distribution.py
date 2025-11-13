# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from flask import Blueprint, render_template, request, jsonify
from modules.api import llama_stack_api

distribution_bp = Blueprint('distribution', __name__, url_prefix='/distribution')


@distribution_bp.route('/providers', methods=['GET'])
def providers():
    """API Providers page"""
    client = llama_stack_api.client
    apis_providers_lst = client.providers.list()
    
    api_to_providers = {}
    for api_provider in apis_providers_lst:
        if api_provider.api in api_to_providers:
            api_to_providers[api_provider.api].append(api_provider.to_dict())
        else:
            api_to_providers[api_provider.api] = [api_provider.to_dict()]
    
    return render_template('distribution/providers.html', api_to_providers=api_to_providers)


@distribution_bp.route('/resources', methods=['GET'])
def resources():
    """Resources page - shows various resource types"""
    resource_type = request.args.get('type', 'models')
    
    client = llama_stack_api.client
    
    context = {
        'resource_type': resource_type,
    }
    
    if resource_type == 'models':
        models = client.models.list()
        context['models'] = [m.to_dict() for m in models]
    elif resource_type == 'vector_dbs':
        vector_dbs = client.vector_dbs.list()
        context['vector_dbs'] = [v.to_dict() for v in vector_dbs]
    elif resource_type == 'shields':
        shields = client.shields.list()
        context['shields'] = [s.to_dict() for s in shields]
    elif resource_type == 'scoring_functions':
        scoring_functions = client.scoring_functions.list()
        context['scoring_functions'] = [sf.to_dict() for sf in scoring_functions]
    elif resource_type == 'datasets':
        datasets = client.datasets.list()
        context['datasets'] = [d.to_dict() for d in datasets]
    elif resource_type == 'benchmarks':
        benchmarks = client.benchmarks.list()
        context['benchmarks'] = [b.to_dict() for b in benchmarks]
    
    return render_template('distribution/resources.html', **context)

