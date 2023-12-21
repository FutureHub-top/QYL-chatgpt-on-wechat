
#!/bin/bash

# copy itchat.pkl from container to host
echo "copy itchat.pkl from container $1 to host data.$2"

# container_id=$(docker ps | awk '{print $1}')
# echo "container_id: $container_id"
# container_name=$(docker ps | awk '{print $6}')
# echo "container_name: $container_name"

container_id=$1
echo "container_id: $container_id"

container_name=$( echo $2 | awk '{print tolower($0)}')
echo "container_name: $container_name"

docker cp $container_id:/app/itchat.pkl ./volumes/app/storage/data.$container_name/itchat.pkl

echo "done!"