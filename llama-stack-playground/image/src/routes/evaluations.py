# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from modules.api import llama_stack_api
from modules.utils import process_dataset
import json
import pandas as pd

evaluations_bp = Blueprint('evaluations', __name__, url_prefix='/evaluations')


@evaluations_bp.route('/app_eval', methods=['GET', 'POST'])
def app_eval():
    """Application evaluation page (Scoring only)"""
    client = llama_stack_api.client
    
    if request.method == 'GET':
        # Get scoring functions
        scoring_functions = client.scoring_functions.list()
        scoring_functions = {sf.identifier: sf for sf in scoring_functions}
        
        return render_template('evaluations/app_eval.html',
                             scoring_functions=scoring_functions)
    
    # Handle file upload
    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename:
            try:
                df = process_dataset(file)
                # Stateless: Return dataset data directly (client can cache if needed)
                return jsonify({
                    "success": True,
                    "dataset": df.to_dict(orient='records'),  # Full dataset included
                    "preview": df.head(10).to_dict(orient='records'),
                    "columns": list(df.columns),
                    "row_count": len(df)
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 400
    
    # Handle evaluation run
    if request.is_json:
        data = request.json
        if data.get('action') == 'run_evaluation':
            selected_scoring_functions = data.get('selected_scoring_functions', [])
            scoring_params = data.get('scoring_params', {})
            num_rows = int(data.get('num_rows', 0))
            
            # Stateless: Get dataset from request (client sends it)
            dataset = data.get('dataset', [])
            rows = dataset if dataset else []
            if num_rows > 0 and num_rows < len(rows):
                rows = rows[:num_rows]
            
            def generate():
                output_res = {}
                total = len(rows)
                
                for i, r in enumerate(rows):
                    try:
                        score_res = llama_stack_api.run_scoring(
                            r,
                            scoring_function_ids=selected_scoring_functions,
                            scoring_params=scoring_params,
                        )
                        
                        for k in r.keys():
                            if k not in output_res:
                                output_res[k] = []
                            output_res[k].append(r[k])
                        
                        for fn_id in selected_scoring_functions:
                            if fn_id not in output_res:
                                output_res[fn_id] = []
                            output_res[fn_id].append(score_res.results[fn_id].score_rows[0])
                        
                        progress = (i + 1) / total
                        yield f"data: {json.dumps({'progress': progress, 'current': i + 1, 'total': total, 'result': score_res.to_json(), 'done': False})}\n\n"
                    except Exception as e:
                        yield f"data: {json.dumps({'error': str(e), 'row': i})}\n\n"
                
                # Convert output_res to DataFrame for final result
                output_df = pd.DataFrame(output_res)
                yield f"data: {json.dumps({'results': output_df.to_dict(orient='records'), 'columns': list(output_df.columns), 'done': True})}\n\n"
            
            return Response(stream_with_context(generate()), mimetype='text/event-stream')
    
    return jsonify({"error": "Invalid request"}), 400


@evaluations_bp.route('/native_eval', methods=['GET', 'POST'])
def native_eval():
    """Native evaluation page (Generation + Scoring)"""
    client = llama_stack_api.client
    
    if request.method == 'GET':
        # Get benchmarks
        benchmarks = client.benchmarks.list()
        benchmarks_dict = {et.identifier: et.to_dict() for et in benchmarks}
        
        # Get available models
        available_models = client.models.list()
        available_models = [model.identifier for model in available_models]
        
        return render_template('evaluations/native_eval.html',
                             benchmarks=benchmarks_dict,
                             models=available_models)
    
    # Handle step 1: Select benchmark
    if request.is_json:
        data = request.json
        action = data.get('action')
        
        if action == 'select_benchmark':
            selected_benchmark = data.get('selected_benchmark')
            benchmarks = client.benchmarks.list()
            benchmarks_dict = {et.identifier: et for et in benchmarks}
            
            if selected_benchmark in benchmarks_dict:
                # Stateless: Return benchmark data directly
                return jsonify({
                    "success": True, 
                    "benchmark": benchmarks_dict[selected_benchmark].to_dict(),
                    "benchmarks": {k: v.to_dict() for k, v in benchmarks_dict.items()}
                })
        
        elif action == 'define_candidate':
            # Stateless: Just acknowledge (client manages state)
            return jsonify({"success": True})
        
        elif action == 'run_evaluation':
            # Stateless: Get all config from request
            selected_benchmark = data.get('selected_benchmark')
            benchmarks_data = data.get('benchmarks', {})
            eval_candidate = data.get('eval_candidate')
            num_rows = int(data.get('num_rows', 5))
            
            if not selected_benchmark or not eval_candidate:
                return jsonify({"error": "Missing benchmark or candidate configuration"}), 400
            
            benchmark_info = benchmarks_data.get(selected_benchmark)
            if not benchmark_info:
                return jsonify({"error": "Benchmark not found"}), 404
            
            dataset_id = benchmark_info.get('dataset_id')
            scoring_functions = benchmark_info.get('scoring_functions', [])
            
            try:
                rows = client.datasets.iterrows(dataset_id=dataset_id)
                rows_data = rows.data
                total_rows = len(rows_data)
                if num_rows < total_rows:
                    rows_data = rows_data[:num_rows]
                
                benchmark_config = {
                    "type": "benchmark",
                    "eval_candidate": eval_candidate,
                    "scoring_params": {},
                }
                
                def generate():
                    output_res = {}
                    total = len(rows_data)
                    
                    for i, r in enumerate(rows_data):
                        try:
                            eval_res = client.eval.evaluate_rows(
                                benchmark_id=selected_benchmark,
                                input_rows=[r],
                                scoring_functions=scoring_functions,
                                benchmark_config=benchmark_config,
                            )
                            
                            for k in r.keys():
                                if k not in output_res:
                                    output_res[k] = []
                                output_res[k].append(r[k])
                            
                            for k in eval_res.generations[0].keys():
                                if k not in output_res:
                                    output_res[k] = []
                                output_res[k].append(eval_res.generations[0][k])
                            
                            for scoring_fn in scoring_functions:
                                if scoring_fn not in output_res:
                                    output_res[scoring_fn] = []
                                output_res[scoring_fn].append(eval_res.scores[scoring_fn].score_rows[0])
                            
                            progress = (i + 1) / total
                            yield f"data: {json.dumps({'progress': progress, 'current': i + 1, 'total': total, 'result': eval_res.to_json(), 'done': False})}\n\n"
                        except Exception as e:
                            yield f"data: {json.dumps({'error': str(e), 'row': i})}\n\n"
                    
                    # Convert output_res to DataFrame for final result
                    output_df = pd.DataFrame(output_res)
                    yield f"data: {json.dumps({'results': output_df.to_dict(orient='records'), 'columns': list(output_df.columns), 'done': True})}\n\n"
                
                return Response(stream_with_context(generate()), mimetype='text/event-stream')
            except Exception as e:
                return jsonify({"error": str(e)}), 500
    
    return jsonify({"error": "Invalid request"}), 400

