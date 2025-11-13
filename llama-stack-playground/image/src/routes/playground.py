# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from modules.api import llama_stack_api
from llama_stack_client import AuthenticationError
import json
import logging

logger = logging.getLogger(__name__)

playground_bp = Blueprint('playground', __name__, url_prefix='/playground')


@playground_bp.route('/chat', methods=['GET', 'POST'])
def chat():
    """Chat playground page"""
    if request.method == 'GET':
        # Stateless: Chat history stored client-side (localStorage)
        # Get available models - handle authentication gracefully
        # NOTE: Don't redirect here - oauth-proxy handles authentication redirects
        # If token is invalid, just skip API calls and render page without models
        try:
            client = llama_stack_api.client
            available_models = client.models.list()
            available_models = [model.identifier for model in available_models if model.model_type == "llm"]
        except Exception as e:
            logger.warning(f"Could not fetch models (token may be expired/invalid): {e}")
            # Return empty list if not authenticated - user will see error message
            # OAuth-proxy will handle redirecting to Keycloak on next request if needed
            available_models = []
        
        # Stateless: No server-side chat history - client manages via localStorage
        return render_template('playground/chat.html', 
                             models=available_models,
                             messages=[])  # Empty - client loads from localStorage
    
    # POST - Handle chat completion
    # NOTE: Don't redirect here - oauth-proxy handles authentication
    # If token is invalid, API call will fail and we'll return error to frontend
    try:
        client = llama_stack_api.client
    except Exception as e:
        logger.error(f"Could not get API client: {e}")
        return jsonify({"error": "Authentication failed. Please refresh the page to re-authenticate."}), 401
    
    data = request.json
    prompt = data.get('prompt')
    model_id = data.get('model_id')
    temperature = float(data.get('temperature', 0.0))
    top_p = float(data.get('top_p', 0.95))
    max_tokens = int(data.get('max_tokens', 512))
    repetition_penalty = float(data.get('repetition_penalty', 1.0))
    system_prompt = data.get('system_prompt', 'You are a helpful AI assistant.')
    stream = data.get('stream', True)
    
    # Stateless: Chat history managed client-side
    # User message is sent by client, response will be streamed back
    
    if temperature > 0.0:
        strategy = {
            "type": "top_p",
            "temperature": temperature,
            "top_p": top_p,
        }
    else:
        strategy = {"type": "greedy"}
    
    if stream:
        def generate():
            full_response = ""
            try:
                response = client.inference.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    model_id=model_id,
                    stream=True,
                    sampling_params={
                        "strategy": strategy,
                        "max_tokens": max_tokens,
                        "repetition_penalty": repetition_penalty,
                    },
                )
                
                for chunk in response:
                    if hasattr(chunk, 'event') and chunk.event.event_type == "progress":
                        if hasattr(chunk.event, 'delta') and hasattr(chunk.event.delta, 'text'):
                            full_response += chunk.event.delta.text
                            yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                
                # Stateless: Client manages chat history via localStorage
                
                yield f"data: {json.dumps({'content': full_response, 'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return Response(stream_with_context(generate()), mimetype='text/event-stream')
    else:
        try:
            response = client.inference.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                model_id=model_id,
                stream=False,
                sampling_params={
                    "strategy": strategy,
                    "max_tokens": max_tokens,
                    "repetition_penalty": repetition_penalty,
                },
            )
            
            full_response = response.completion_message.content
            # Stateless: Client manages chat history via localStorage
            
            return jsonify({"content": full_response, "done": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@playground_bp.route('/chat/clear', methods=['POST'])
def clear_chat():
    """Clear chat messages (stateless - client handles localStorage)"""
    return jsonify({"success": True})


@playground_bp.route('/rag', methods=['GET', 'POST'])
def rag():
    """RAG playground page"""
    # NOTE: Don't redirect here - oauth-proxy handles authentication
    # If token is invalid, API calls will fail gracefully
    if request.method == 'GET':
        # Stateless: RAG history managed client-side
        
        # Check if token is available and valid before making API calls
        jwt_token = llama_stack_api._get_jwt_token()
        if not jwt_token:
            # Token expired or missing - return page with empty lists
            # Frontend will show error message or trigger re-auth
            logger.warning("No valid JWT token available for RAG page - returning empty lists")
            return render_template('playground/rag.html',
                                 models=[],
                                 vector_dbs=[],
                                 messages=[])  # Stateless - client manages via localStorage
        
        # Get available models and vector DBs
        try:
            client = llama_stack_api.client
            available_models = client.models.list()
            available_models = [model.identifier for model in available_models if model.model_type == "llm"]
            vector_dbs = client.vector_dbs.list()
            vector_dbs = [vector_db.identifier for vector_db in vector_dbs]
        except Exception as e:
            logger.error(f"Error fetching models/vector_dbs for RAG page: {e}")
            # Return empty lists on error - user will see error message
            available_models = []
            vector_dbs = []
        
        return render_template('playground/rag.html',
                             models=available_models,
                             vector_dbs=vector_dbs,
                             messages=[])  # Stateless - client manages via localStorage
    
    # Handle file upload for document collection creation
    if 'files' in request.files:
        # Check token before processing
        jwt_token = llama_stack_api._get_jwt_token()
        if not jwt_token:
            return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
        
        files = request.files.getlist('files')
        vector_db_name = request.form.get('vector_db_name', 'rag_vector_db')
        
        if files and files[0].filename:
            try:
                from modules.utils import data_url_from_file
                client = llama_stack_api.client
                
                # Find vector_io provider
                providers = client.providers.list()
                vector_io_provider = None
                for x in providers:
                    if x.api == "vector_io":
                        vector_io_provider = x.provider_id
                        break
                
                if not vector_io_provider:
                    return jsonify({"success": False, "error": "No vector_io provider found"}), 500
                
                client.vector_dbs.register(
                    vector_db_id=vector_db_name,
                    embedding_dimension=384,
                    embedding_model="all-MiniLM-L6-v2",
                    provider_id=vector_io_provider,
                )
                
                # Insert documents
                from llama_stack_client import RAGDocument
                documents = [
                    RAGDocument(
                        document_id=f.filename,
                        content=data_url_from_file(f),
                    )
                    for f in files
                ]
                client.tool_runtime.rag_tool.insert(
                    vector_db_id=vector_db_name,
                    documents=documents,
                    chunk_size_in_tokens=512,
                )
                
                return jsonify({"success": True, "message": "Vector database created successfully!"})
            except AuthenticationError as e:
                logger.error(f"Authentication error creating document collection: {e}")
                return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
            except Exception as e:
                logger.error(f"Error creating document collection: {e}", exc_info=True)
                return jsonify({"success": False, "error": str(e)}), 500
    
    return jsonify({"error": "Invalid request"}), 400


@playground_bp.route('/rag/query', methods=['POST'])
def rag_query():
    """Handle RAG query"""
    data = request.json
    prompt = data.get('prompt')
    rag_mode = data.get('rag_mode', 'Direct')
    selected_vector_dbs = data.get('selected_vector_dbs', [])
    selected_model = data.get('selected_model')
    temperature = float(data.get('temperature', 0.0))
    top_p = float(data.get('top_p', 0.95))
    system_prompt = data.get('system_prompt', 'You are a helpful assistant.')
    
    client = llama_stack_api.client
    
    # Stateless: RAG history managed client-side
    
    if rag_mode == "Agent-based":
        # Agent-based RAG (simplified for Flask - would need SSE streaming)
        return jsonify({"error": "Agent-based RAG streaming not yet implemented in Flask"}), 501
    else:
        # Direct RAG
        try:
            # Query vector DB
            rag_response = client.tool_runtime.rag_tool.query(
                content=prompt, vector_db_ids=selected_vector_dbs
            )
            prompt_context = rag_response.content
            
            # Construct extended prompt
            extended_prompt = f"Please answer the following query using the context below.\n\nCONTEXT:\n{prompt_context}\n\nQUERY:\n{prompt}"
            
            # Stateless: Build messages on-demand (no server-side storage)
            rag_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": extended_prompt}
            ]
            
            if temperature > 0.0:
                strategy = {"type": "top_p", "temperature": temperature, "top_p": top_p}
            else:
                strategy = {"type": "greedy"}
            
            def generate():
                full_response = ""
                try:
                    response = client.inference.chat_completion(
                        messages=rag_messages,
                        model_id=selected_model,
                        sampling_params={"strategy": strategy},
                        stream=True,
                    )
                    
                    for chunk in response:
                        if hasattr(chunk.event, 'delta') and hasattr(chunk.event.delta, 'text'):
                            full_response += chunk.event.delta.text
                            yield f"data: {json.dumps({'content': full_response, 'context': prompt_context, 'done': False})}\n\n"
                    
                    response_dict = {"role": "assistant", "content": full_response, "stop_reason": "end_of_message"}
                    # Stateless: Client manages displayed messages via localStorage
                    
                    yield f"data: {json.dumps({'content': full_response, 'context': prompt_context, 'done': True})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
            return Response(stream_with_context(generate()), mimetype='text/event-stream')
        except AuthenticationError as e:
            logger.error(f"Authentication error in RAG query: {e}")
            return jsonify({"error": "Authentication token expired. Please refresh the page to re-authenticate."}), 401
        except Exception as e:
            logger.error(f"Error in RAG query: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500


@playground_bp.route('/rag/clear', methods=['POST'])
def clear_rag():
    """Clear RAG chat (stateless - client handles localStorage)"""
    return jsonify({"success": True})


@playground_bp.route('/tools', methods=['GET', 'POST'])
def tools():
    """Tools playground page"""
    # NOTE: Don't redirect here - oauth-proxy handles authentication
    # If token is invalid, API calls will fail gracefully
    if request.method == 'GET':
        # Check if token is available and valid before making API calls
        jwt_token = llama_stack_api._get_jwt_token()
        if not jwt_token:
            # Token expired or missing - return page with empty lists
            # Frontend will show error message or trigger re-auth
            logger.warning("No valid JWT token available for Tools page - returning empty lists")
            return render_template('playground/tools.html',
                                models=[],
                                builtin_tools=[],
                                mcp_tools=[],
                                messages=[])  # Empty - client loads from localStorage
        
        # Get models and tool groups
        try:
            client = llama_stack_api.client
            models = client.models.list()
            model_list = [model.identifier for model in models if model.model_type == "llm"]
            
            tool_groups = client.toolgroups.list()
            tool_groups_list = [tool_group.identifier for tool_group in tool_groups]
            mcp_tools_list = [tool for tool in tool_groups_list if tool.startswith("mcp::")]
            builtin_tools_list = [tool for tool in tool_groups_list if not tool.startswith("mcp::")]
        except Exception as e:
            logger.error(f"Error fetching models/tool_groups for Tools page: {e}")
            # Return empty lists on error - user will see error message
            model_list = []
            mcp_tools_list = []
            builtin_tools_list = []
        
        # Stateless: Tools history managed client-side
        return render_template('playground/tools.html',
                            models=model_list,
                            builtin_tools=builtin_tools_list,
                            mcp_tools=mcp_tools_list,
                            messages=[])  # Empty - client loads from localStorage
    
    # Handle tool query (POST)
    try:
        # Check token before processing
        jwt_token = llama_stack_api._get_jwt_token()
        if not jwt_token:
            return jsonify({"error": "Authentication token expired. Please refresh the page to re-authenticate."}), 401
        
        data = request.json
        if not data:
            return jsonify({"error": "Invalid request: missing JSON data"}), 400
        
        prompt = data.get('prompt')
        model = data.get('model')
        toolgroup_selection = data.get('toolgroup_selection', [])
        selected_vector_dbs = data.get('selected_vector_dbs', [])
        agent_type = data.get('agent_type', 'Regular')
        max_tokens = int(data.get('max_tokens', 512))
        openshift_token = data.get('openshift_token')  # Optional OpenShift token for MCP headers
        
        # Create client with optional OpenShift token for MCP headers
        # If openshift_token is provided and not empty, use it; otherwise use JWT token
        if openshift_token and openshift_token.strip():
            client = llama_stack_api.client_with_openshift_token(openshift_token.strip())
        else:
            client = llama_stack_api.client
    
        # Process tool selection (handle RAG tools)
        tools_config = []
        for tool_name in toolgroup_selection:
            if tool_name == "builtin::rag":
                tool_dict = {
                    "name": "builtin::rag/knowledge_search",
                    "args": {
                        "vector_db_ids": list(selected_vector_dbs),
                    },
                }
                tools_config.append(tool_dict)
            else:
                tools_config.append(tool_name)
        
        # Stateless: Tool messages and agent state managed client-side
        # Agent session created fresh each time (no persistence)
        agent_key = f"agent_{model}_{agent_type}_{str(sorted(toolgroup_selection))}_{max_tokens}"
        
        from llama_stack_client import Agent
        from llama_stack_client.lib.agents.react.agent import ReActAgent
        from llama_stack_client.lib.agents.react.tool_parser import ReActOutput
        
        # Create agent
        if agent_type == "ReAct":
            agent = ReActAgent(
                client=client,
                model=model,
                tools=tools_config,
                response_format={
                    "type": "json_schema",
                    "json_schema": ReActOutput.model_json_schema(),
                },
                sampling_params={"strategy": {"type": "greedy"}, "max_tokens": max_tokens},
            )
        else:
            agent = Agent(
                client,
                model=model,
                instructions="You are a helpful assistant. When you use a tool always respond with a summary of the result.",
                tools=tools_config,
                sampling_params={"strategy": {"type": "greedy"}, "max_tokens": max_tokens},
            )
        
        # Stateless: Create fresh agent session each time (no persistence)
        import uuid
        session_id = agent.create_session(session_name=f"tool_demo_{uuid.uuid4()}")
        
        def generate():
            try:
                # Ensure we have a valid agent
                if agent is None:
                    logger.error("Agent is None")
                    yield f"data: {json.dumps({'error': 'Agent not initialized', 'done': True})}\n\n"
                    return
                
                # Prepare request data
                request_data = {
                    "session_id": session_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True
                }
                
                logger.info(f"Creating turn - Request: session_id={session_id}, prompt={prompt[:100]}...")
                logger.info(f"Request details: agent_type={agent_type}, model={model}, tools={len(tools_config)}")
                
                # Log raw request
                import json as json_module
                try:
                    request_json = json_module.dumps(request_data, indent=2, default=str)
                    logger.debug(f"Raw API Request:\n{request_json}")
                except Exception as e:
                    logger.debug(f"Could not serialize request: {e}")
                
                turn_response = agent.create_turn(
                    session_id=session_id,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True,
                )
                
                logger.info(f"Turn response generator created: {type(turn_response)}")
                
                # Check if turn_response is None or empty
                if turn_response is None:
                    logger.error("turn_response is None")
                    yield f"data: {json.dumps({'error': 'No response from agent', 'done': True})}\n\n"
                    return
                
                logger.info("Starting to process turn_response")
                
                if agent_type == "ReAct":
                    yield from _handle_react_response(turn_response)
                else:
                    yield from _handle_regular_response(turn_response)
                logger.info("Finished processing turn_response")
            except StopIteration:
                # Normal end of stream
                logger.info("StopIteration - end of stream")
                yield f"data: {json.dumps({'content': '', 'done': True})}\n\n"
            except Exception as e:
                import traceback
                logger.error(f"Exception in generate: {str(e)}\n{traceback.format_exc()}")
                error_details = {
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                    'agent_type': agent_type,
                    'prompt': prompt[:100] if prompt else ''
                }
                yield f"data: {json.dumps({'error': error_details, 'done': True})}\n\n"
        
        return Response(stream_with_context(generate()), mimetype='text/event-stream')
    except AuthenticationError as e:
        logger.error(f"Authentication error in Tools query: {e}")
        return jsonify({"error": "Authentication token expired. Please refresh the page to re-authenticate."}), 401
    except Exception as e:
        logger.error(f"Error initializing agent: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _handle_react_response(turn_response):
    """Handle ReAct agent response"""
    
    current_step_content = ""
    final_answer = None
    tool_results = []
    full_response = ""
    
    for response in turn_response:
        if not hasattr(response.event, "payload"):
            yield f"data: {json.dumps({'error': 'Missing payload attribute', 'details': str(response)})}\n\n"
            return
        
        payload = response.event.payload
        
        if payload.event_type == "step_progress":
            # Try multiple ways to get text from delta
            text = None
            if hasattr(payload, 'delta') and hasattr(payload.delta, "text"):
                text = payload.delta.text
            elif hasattr(payload, 'delta') and hasattr(payload.delta, "content"):
                text = payload.delta.content
            else:
                # Log for debugging
                logger.debug(f"step_progress delta attributes: {[x for x in dir(payload.delta) if not x.startswith('_')] if hasattr(payload, 'delta') else 'no delta'}")
            
            if text:
                current_step_content += str(text)
                full_response += str(text)
                # Stream accumulated content so far to frontend
                yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
            continue
        
        if payload.event_type == "step_complete":
            step_details = payload.step_details
            
            if step_details.step_type == "inference":
                # Process inference step
                try:
                    react_output_data = json.loads(current_step_content)
                    thought = react_output_data.get("thought")
                    action = react_output_data.get("action")
                    answer = react_output_data.get("answer")
                    
                    if answer and answer != "null" and answer is not None:
                        final_answer = answer
                        full_response += f"\n\n✅ **Final Answer:**\n{answer}"
                    
                    if thought:
                        yield f"data: {json.dumps({'thought': thought, 'done': False})}\n\n"
                    
                    if action and isinstance(action, dict):
                        tool_name = action.get("tool_name")
                        tool_params = action.get("tool_params")
                        yield f"data: {json.dumps({'action': {'tool_name': tool_name, 'tool_params': tool_params}, 'done': False})}\n\n"
                    
                    if answer and answer != "null" and answer is not None:
                        yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                
                except json.JSONDecodeError:
                    yield f"data: {json.dumps({'error': 'Failed to parse ReAct step', 'content': current_step_content})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': f'Failed to process ReAct step: {str(e)}'})}\n\n"
                
                current_step_content = ""
            
            elif step_details.step_type == "tool_execution":
                # Process tool execution
                new_tool_results = _process_tool_execution(step_details, [])
                tool_results.extend(new_tool_results)
                for tool_result in new_tool_results:
                    # Serialize tool_result properly - extract text from TextContentItem if needed
                    tool_name, tool_content = tool_result
                    # Convert to string if it's not already
                    if hasattr(tool_content, 'text'):
                        tool_content = tool_content.text
                    elif hasattr(tool_content, 'content'):
                        tool_content = tool_content.content
                    elif not isinstance(tool_content, (str, int, float, bool, type(None))):
                        # Try to convert to string
                        tool_content = str(tool_content)
                    
                    serializable_result = (tool_name, tool_content)
                    yield f"data: {json.dumps({'tool_result': serializable_result, 'done': False})}\n\n"
                current_step_content = ""
            else:
                current_step_content = ""
    
    if not final_answer and tool_results:
        summary = _format_tool_results_summary(tool_results)
        full_response += "\n\n**Here's what I found:**\n" + summary
        yield f"data: {json.dumps({'content': summary, 'done': False})}\n\n"
    
    # Stateless: Client manages tool messages via localStorage
    yield f"data: {json.dumps({'content': full_response or 'No response generated', 'done': True})}\n\n"


def _handle_regular_response(turn_response):
    """Handle regular agent response - matches original Streamlit implementation exactly"""
    
    full_response = ""
    has_content = False
    event_count = 0
    
    try:
        for response in turn_response:
            event_count += 1
            logger.info(f"Processing event #{event_count}, type: {type(response)}")
            
            # Log raw response structure
            try:
                response_str = str(response)[:500] if hasattr(response, '__str__') else str(type(response))
                logger.debug(f"Raw response #{event_count}: {response_str}")
            except:
                pass
            
            # Log raw response for debugging
            try:
                logger.debug(f"Response #{event_count} - has event: {hasattr(response, 'event')}")
                if hasattr(response, 'event'):
                    logger.debug(f"Response #{event_count} - event type: {type(response.event)}")
                    logger.debug(f"Response #{event_count} - event dir: {[x for x in dir(response.event) if not x.startswith('_')]}")
            except:
                pass
            
            if hasattr(response, 'event') and hasattr(response.event, "payload"):
                payload = response.event.payload
                
                # Log raw payload structure
                try:
                    payload_attrs = [x for x in dir(payload) if not x.startswith('_')]
                    logger.info(f"Payload #{event_count} - event_type: {payload.event_type}, attributes: {payload_attrs}")
                    payload_str = str(payload)[:500] if hasattr(payload, '__str__') else str(type(payload))
                    logger.debug(f"Raw payload #{event_count}: {payload_str}")
                except Exception as e:
                    logger.warning(f"Could not log payload: {e}")
                
                # Handle all event types
                event_type = payload.event_type
                logger.info(f"Processing event type: {event_type}")
                
                # Handle step_progress - yield text immediately and accumulate
                if event_type == "step_progress":
                    logger.info(f"step_progress event, has delta: {hasattr(payload, 'delta')}")
                    
                    # Log raw payload structure
                    try:
                        payload_attrs = {k: str(v)[:200] for k, v in payload.__dict__.items() if k != '_sa_instance_state'}
                        logger.debug(f"step_progress payload attributes: {payload_attrs}")
                    except:
                        logger.debug(f"step_progress payload type: {type(payload)}, dir: {[x for x in dir(payload) if not x.startswith('_')]}")
                    
                    if hasattr(payload, "delta"):
                        delta_attrs = [x for x in dir(payload.delta) if not x.startswith('_')]
                        logger.info(f"delta attributes: {delta_attrs}")
                        
                        if hasattr(payload.delta, "text"):
                            text_delta = payload.delta.text
                            if text_delta:
                                logger.info(f"Got text delta ({len(text_delta)} chars): {text_delta[:100]}...")
                                full_response += text_delta
                                has_content = True
                                yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                        else:
                            logger.warning(f"Delta exists but no text attr. Delta type: {type(payload.delta)}, attrs: {delta_attrs}")
                            # Try alternative attribute names
                            if hasattr(payload.delta, 'content'):
                                content = payload.delta.content
                                if content:
                                    logger.info(f"Got content delta: {content[:100]}...")
                                    full_response += str(content)
                                    has_content = True
                                    yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                    else:
                        logger.warning("No delta in step_progress")
                
                # Handle step_start - log it
                if event_type == "step_start":
                    logger.info(f"step_start event")
                
                # Handle turn_start - log it
                if event_type == "turn_start":
                    logger.info(f"turn_start event")
                
                # Handle turn_complete - this should have the final turn data
                if event_type == "turn_complete":
                    logger.info("turn_complete event - turn finished")
                    try:
                        if hasattr(payload, 'turn') and hasattr(payload.turn, 'output_message'):
                            output_msg = payload.turn.output_message
                            if hasattr(output_msg, 'content'):
                                final_text = output_msg.content
                                if isinstance(final_text, str):
                                    full_response += final_text
                                    has_content = True
                                    yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                                    logger.info(f"Got final content from turn_complete: {len(final_text)} chars")
                                elif isinstance(final_text, list):
                                    for item in final_text:
                                        if hasattr(item, 'text'):
                                            full_response += item.text
                                        elif isinstance(item, str):
                                            full_response += item
                                    has_content = True
                                    yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                                    logger.info(f"Got final content from turn_complete (list): {len(full_response)} chars")
                    except Exception as e:
                        logger.warning(f"Error extracting content from turn_complete: {e}")
                
                # Handle step_complete - use 'if' not 'elif' (can happen in same iteration per original)
                if event_type == "step_complete":
                    logger.info(f"step_complete event")
                    if hasattr(payload, "step_details"):
                        step_details = payload.step_details
                        step_type = getattr(step_details, 'step_type', 'unknown')
                        logger.info(f"step_type: {step_type}")
                        
                        if step_type == "tool_execution":
                            logger.info("Processing tool_execution step")
                            
                            # Log raw step_details structure
                            try:
                                step_details_attrs = {k: str(v)[:200] for k, v in step_details.__dict__.items() if k != '_sa_instance_state'}
                                logger.debug(f"tool_execution step_details attributes: {step_details_attrs}")
                            except:
                                step_details_dir = [x for x in dir(step_details) if not x.startswith('_')]
                                logger.debug(f"step_details attributes: {step_details_dir}")
                            
                            # Try multiple ways to get tool name
                            tool_name = None
                            if hasattr(step_details, 'tool_calls') and step_details.tool_calls:
                                logger.info(f"Found tool_calls: {len(step_details.tool_calls)}")
                                tool_name = str(step_details.tool_calls[0].tool_name)
                                logger.info(f"Tool name: {tool_name}")
                                yield f"data: {json.dumps({'tool_info': f'Using "{tool_name}" tool', 'done': False})}\n\n"
                            else:
                                logger.warning("No tool_calls found in step_details")
                                yield f"data: {json.dumps({'tool_info': 'No tool_calls present in step_details', 'done': False})}\n\n"
                            
                            # Process tool responses - these should be included in the response
                            if hasattr(step_details, 'tool_responses'):
                                logger.info(f"Has tool_responses: {step_details.tool_responses}")
                                if step_details.tool_responses:
                                    logger.info(f"Number of tool responses: {len(step_details.tool_responses)}")
                                    for idx, tool_response_obj in enumerate(step_details.tool_responses):
                                        logger.info(f"Processing tool_response #{idx+1}: {type(tool_response_obj)}")
                                        tool_result_name = getattr(tool_response_obj, 'tool_name', 'unknown')
                                        tool_result_content = getattr(tool_response_obj, 'content', '')
                                        
                                        logger.info(f"Tool response: name={tool_result_name}, content_type={type(tool_result_content)}")
                                        
                                        # Log raw content structure
                                        try:
                                            if hasattr(tool_result_content, '__dict__'):
                                                content_attrs = {k: str(v)[:200] for k, v in tool_result_content.__dict__.items() if k != '_sa_instance_state'}
                                                logger.debug(f"tool_result_content attributes: {content_attrs}")
                                            else:
                                                logger.debug(f"tool_result_content: {str(tool_result_content)[:500]}")
                                        except Exception as e:
                                            logger.debug(f"Could not log tool_result_content: {e}")
                                        
                                        # Extract text from TextContentItem or similar objects
                                        original_content = tool_result_content
                                        if hasattr(tool_result_content, 'text'):
                                            tool_result_content = tool_result_content.text
                                            logger.info(f"Extracted text from TextContentItem: {len(tool_result_content)} chars")
                                        elif hasattr(tool_result_content, 'content'):
                                            tool_result_content = tool_result_content.content
                                            logger.info(f"Extracted content attribute: {len(str(tool_result_content))} chars")
                                        elif isinstance(tool_result_content, list):
                                            # Handle list of content items
                                            logger.info(f"Content is a list with {len(tool_result_content)} items")
                                            text_parts = []
                                            for item in tool_result_content:
                                                if hasattr(item, 'text'):
                                                    text_parts.append(item.text)
                                                elif isinstance(item, str):
                                                    text_parts.append(item)
                                                else:
                                                    text_parts.append(str(item))
                                            tool_result_content = '\n'.join(text_parts)
                                            logger.info(f"Extracted {len(tool_result_content)} chars from list")
                                        elif not isinstance(tool_result_content, (str, int, float, bool, type(None))):
                                            logger.warning(f"Content type {type(tool_result_content)} not directly serializable, converting to string")
                                            tool_result_content = str(tool_result_content)
                                        
                                        # Tool results are typically used by the model in subsequent inference
                                        # We'll let the model's response include them naturally
                                        # But we can also show them separately
                                        if tool_result_content and isinstance(tool_result_content, str):
                                            logger.info(f"Yielding tool result: {tool_result_name} ({len(tool_result_content)} chars)")
                                            # Show tool result in UI as expandable detail
                                            yield f"data: {json.dumps({'tool_result': (tool_result_name, tool_result_content), 'done': False})}\n\n"
                                        else:
                                            logger.warning(f"Tool result content is not a string or is empty: {type(tool_result_content)}")
                                else:
                                    logger.warning("tool_responses exists but is empty/None")
                            else:
                                logger.warning("No tool_responses attribute in step_details")
                        elif step_type == "inference":
                            logger.info("Inference step completed")
                            # Inference steps may have output content - check if available
                            try:
                                if hasattr(step_details, 'output') and step_details.output:
                                    output = step_details.output
                                    if hasattr(output, 'content'):
                                        inference_content = output.content
                                        if isinstance(inference_content, str):
                                            full_response += inference_content
                                            has_content = True
                                            yield f"data: {json.dumps({'content': full_response, 'done': False})}\n\n"
                                            logger.info(f"Got inference output: {len(inference_content)} chars")
                            except Exception as e:
                                logger.debug(f"Could not extract inference output: {e}")
                    else:
                        logger.info("step_complete but no step_details")
            else:
                logger.warning(f"Response without payload, event structure: {hasattr(response, 'event')}")
                # Original code yields error message
                yield f"data: {json.dumps({'error': f'Error occurred in the Llama Stack Cluster: {response}', 'done': False})}\n\n"
        
        logger.info(f"Processed {event_count} events total")
        logger.info(f"Response summary: has_content={has_content}, full_response length={len(full_response)}")
        
        # If we have no content but processed events, log a warning
        if not has_content and event_count > 0:
            logger.warning(f"Processed {event_count} events but no content extracted! This might indicate a response structure issue.")
    
    except StopIteration:
        logger.info("StopIteration caught - end of generator")
        # This is normal when generator ends
    except Exception as e:
        import traceback
        logger.error(f"Exception in _handle_regular_response: {str(e)}\n{traceback.format_exc()}")
        error_msg = f"Error processing response: {str(e)}\n{traceback.format_exc()}"
        yield f"data: {json.dumps({'error': error_msg, 'done': False})}\n\n"
    
    # Stateless: Client manages tool messages via localStorage
    
    final_content = full_response if has_content else "No response generated"
    logger.info(f"Final content length: {len(final_content)}")
    
    yield f"data: {json.dumps({'content': final_content, 'done': True})}\n\n"


def _process_tool_execution(step_details, tool_results):
    """Process tool execution step details"""
    try:
        if hasattr(step_details, "tool_responses") and step_details.tool_responses:
            for tool_response in step_details.tool_responses:
                tool_name = getattr(tool_response, 'tool_name', 'unknown')
                content = getattr(tool_response, 'content', '')
                
                # Extract text from TextContentItem or similar objects
                if hasattr(content, 'text'):
                    content = content.text
                elif hasattr(content, 'content'):
                    content = content.content
                elif isinstance(content, list):
                    # Handle list of content items
                    text_parts = []
                    for item in content:
                        if hasattr(item, 'text'):
                            text_parts.append(item.text)
                        elif isinstance(item, str):
                            text_parts.append(item)
                        else:
                            text_parts.append(str(item))
                    content = '\n'.join(text_parts)
                elif not isinstance(content, (str, int, float, bool, type(None))):
                    # Convert to string as fallback
                    content = str(content)
                
                tool_results.append((tool_name, content))
    except Exception as e:
        logger.error(f"Error processing tool execution: {e}")
    return tool_results


def _format_tool_results_summary(tool_results):
    """Format tool results as summary text"""
    summary_parts = []
    for tool_name, content in tool_results:
        try:
            parsed_content = json.loads(content)
            if tool_name == "web_search" and "top_k" in parsed_content:
                for i, result in enumerate(parsed_content["top_k"][:3], 1):
                    title = result.get("title", "Untitled")
                    url = result.get("url", "")
                    content_text = result.get("content", "").strip()
                    summary_parts.append(f"\n- **{title}**\n  {content_text}\n  [Source]({url})\n")
            elif "results" in parsed_content and isinstance(parsed_content["results"], list):
                for i, result in enumerate(parsed_content["results"][:3], 1):
                    if isinstance(result, dict):
                        name = result.get("name", result.get("title", f"Result {i}"))
                        description = result.get("description", result.get("content", result.get("summary", "")))
                        summary_parts.append(f"\n- **{name}**\n  {description}\n")
                    else:
                        summary_parts.append(f"\n- {result}\n")
            elif isinstance(parsed_content, dict) and len(parsed_content) > 0:
                summary_parts.append("\n```\n")
                for key, value in list(parsed_content.items())[:5]:
                    if isinstance(value, str) and len(value) < 100:
                        summary_parts.append(f"{key}: {value}\n")
                    else:
                        summary_parts.append(f"{key}: [Complex data]\n")
                summary_parts.append("```\n")
            elif isinstance(parsed_content, list) and len(parsed_content) > 0:
                for item in parsed_content[:3]:
                    if isinstance(item, str):
                        summary_parts.append(f"- {item}\n")
                    elif isinstance(item, dict) and "text" in item:
                        summary_parts.append(f"- {item['text']}\n")
        except (json.JSONDecodeError, TypeError, AttributeError, KeyError, IndexError):
            summary_parts.append(f"\n**{tool_name}** was used but returned complex data.\n")
    
    return "".join(summary_parts)


@playground_bp.route('/tools/clear', methods=['POST'])
def clear_tools():
    """Clear tools chat (stateless - client handles localStorage)"""
    return jsonify({"success": True})


@playground_bp.route('/tools/get_tools', methods=['POST'])
def get_tools():
    """Get tools for selected toolgroups"""
    # Check token before processing
    jwt_token = llama_stack_api._get_jwt_token()
    if not jwt_token:
        return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
    
    data = request.json
    toolgroup_ids = data.get('toolgroup_ids', [])
    
    try:
        client = llama_stack_api.client
        grouped_tools = {}
        total_tools = 0
        
        for toolgroup_id in toolgroup_ids:
            try:
                tools = client.tools.list(toolgroup_id=toolgroup_id)
                grouped_tools[toolgroup_id] = [tool.identifier for tool in tools]
                total_tools += len(tools)
            except Exception as e:
                logger.error(f"Error fetching tools for toolgroup {toolgroup_id}: {e}", exc_info=True)
                grouped_tools[toolgroup_id] = []
        
        return jsonify({
            "grouped_tools": grouped_tools,
            "total_tools": total_tools
        })
    except AuthenticationError as e:
        logger.error(f"Authentication error fetching tools: {e}")
        return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
    except Exception as e:
        logger.error(f"Error fetching tools: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@playground_bp.route('/tools/get_vector_dbs', methods=['GET'])
def get_vector_dbs():
    """Get available vector databases"""
    # Check token before processing
    jwt_token = llama_stack_api._get_jwt_token()
    if not jwt_token:
        return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
    
    try:
        client = llama_stack_api.client
        vector_dbs = client.vector_dbs.list() or []
        vector_dbs_list = [vector_db.identifier for vector_db in vector_dbs]
        return jsonify({"vector_dbs": vector_dbs_list})
    except AuthenticationError as e:
        logger.error(f"Authentication error fetching vector DBs: {e}")
        return jsonify({"error": "Authentication token expired. Please refresh the page."}), 401
    except Exception as e:
        logger.error(f"Error fetching vector DBs: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

