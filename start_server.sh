#!/bin/bash
# Start Minecraft server in background and wait for RCON

cd "$(dirname "$0")"/simulation/server

echo "Starting Minecraft server..."
nohup java -Xmx4G -jar fabric-server-launch.jar nogui > server.log 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > server.pid

echo "Server PID: $SERVER_PID"
echo "Waiting for server to start (RCON on port 25575)..."

# Wait for RCON to be available
for i in {1..60}; do
    if nc -z localhost 25575 2>/dev/null; then
        echo "Server is ready after ${i} seconds!"
        exit 0
    fi
    sleep 1
    echo "Waiting... ($i/60)"
done

echo "Server failed to start. Check server.log"
tail -50 server.log
exit 1
