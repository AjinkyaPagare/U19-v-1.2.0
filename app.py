# Flask imports - no monkey patching needed for threading mode
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import os

# Initialize Flask App with static frontend folder
app = Flask(__name__, static_folder='frontend', static_url_path='')
app.config['SECRET_KEY'] = 'secret!'

# Initialize SocketIO - auto-detect async mode
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

# Track rooms and their participants
active_rooms = {}  # {room_code: {'senders': [sid1], 'receivers': [sid2]}}

@app.route('/api/health')
def health_check():
    return jsonify({
        "status": "online", 
        "message": "Secure Text Sync Backend is Running! (Room Support Enabled)"
    })


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    # Let Socket.IO endpoints pass through to the Socket.IO server
    if path.startswith('socket.io'):
        abort(404)

    static_dir = app.static_folder
    target_path = os.path.join(static_dir, path)

    if path and os.path.exists(target_path):
        return send_from_directory(static_dir, path)

    return send_from_directory(static_dir, 'index.html')

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f'Client disconnected: {sid}')
    
    # Remove from all rooms and notify
    for room_code in list(active_rooms.keys()):
        room = active_rooms[room_code]
        was_sender = sid in room.get('senders', [])
        was_receiver = sid in room.get('receivers', [])
        
        if was_sender:
            room['senders'].remove(sid)
            # Notify receivers that sender left
            if room.get('receivers'):
                socketio.emit('room_status', {
                    'status': 'sender_left',
                    'message': 'Sender has left the room'
                }, to=f"{room_code}_recv")
        
        if was_receiver:
            room['receivers'].remove(sid)
        
        # Clean up empty rooms
        if not room['senders'] and not room['receivers']:
            del active_rooms[room_code]
            print(f"Room {room_code} deleted (empty)")

@socketio.on('join_room')
def on_join(data):
    """Allow client to join with strict role separation"""
    room_code = data.get('code')
    device_type = data.get('type')  # 'sender' or 'receiver'
    sid = request.sid
    
    if not room_code or not device_type:
        emit('error', {'message': 'Invalid room code or device type'})
        return
    
    # Initialize room if it doesn't exist
    if room_code not in active_rooms:
        active_rooms[room_code] = {'senders': [], 'receivers': []}
    
    room = active_rooms[room_code]
    
    # Add to appropriate list
    if device_type == 'sender':
        if sid not in room['senders']:
            room['senders'].append(sid)
        join_room(room_code)
        print(f"📱 Sender {sid} joined room: {room_code}")
    elif device_type == 'receiver':
        if sid not in room['receivers']:
            room['receivers'].append(sid)
        join_room(room_code)
        join_room(f"{room_code}_recv")
        print(f"🔊 Receiver {sid} joined room: {room_code}")
    
    # Check if BOTH sender and receiver are present
    has_sender = len(room['senders']) > 0
    has_receiver = len(room['receivers']) > 0
    room_active = has_sender and has_receiver
    
    # Send status to the joining client
    emit('room_joined', {
        'message': f'{device_type} joined',
        'type': device_type,
        'room_active': room_active,
        'has_sender': has_sender,
        'has_receiver': has_receiver
    })
    
    # Notify others in the room if room becomes active
    if room_active:
        emit('room_status', {
            'status': 'active',
            'message': 'Both sender and receiver connected'
        }, to=room_code, skip_sid=sid)

@socketio.on('send_text')
def handle_text(data):
    """Send text ONLY to Receivers"""
    room_code = data.get('code')
    text = data.get('text', '')
    
    if room_code and text:
        # Check if room exists and has receivers
        if room_code in active_rooms and active_rooms[room_code]['receivers']:
            print(f"Transmission to {room_code}: {text[:20]}...")
            emit('receive_text', data, to=f"{room_code}_recv")
        else:
            emit('error', {'message': 'No receivers in room'})

@socketio.on('send_live_control')
def handle_live_control(data):
    """Relay live typing control events from sender to receivers"""
    room_code = data.get('code')
    control = data.get('control')

    if not room_code or not control:
        emit('error', {'message': 'Invalid live control payload'})
        return

    if room_code in active_rooms and active_rooms[room_code]['receivers']:
        print(f"Live control '{control}' for room {room_code}")
        emit('receive_live_control', data, to=f"{room_code}_recv")
    else:
        emit('error', {'message': 'No receivers in room'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
