
#!/bin/bash

# copy itchat.pkl from container to host
echo "copy itchat.pkl from host data.$1 to container $2"

container_name=$( echo $1 | awk '{print tolower($0)}')
echo "container_name: $container_name"

container_id=$2
echo "container_id: $container_id"

docker cp ./volumes/app/storage/data.$container_name/itchat.pkl $container_id:/app/itchat.pkl

echo "done!"