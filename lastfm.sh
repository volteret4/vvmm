#!/bin/bash


# Configuración
API_KEY="5fa1d91a9fd9c425b3c584911d893ae8"
ARTIST="${1}"
ALBUM="${2}"

# Verifica que los parámetros no estén vacíos
if [ -z "$ARTIST" ] || [ -z "$ALBUM" ]; then
    echo "Por favor, proporciona un nombre de artista y álbum."
    exit 1
fi

# URL de la API de Last.fm para buscar álbumes
API_URL="http://ws.audioscrobbler.com/2.0/?method=album.search&album=$ALBUM&artist=$ARTIST&api_key=$API_KEY&format=json"

# Realiza la solicitud a la API de Last.fm
response=$(curl -s "$API_URL")

# Verifica si la respuesta contiene un error
if echo "$response" | grep -q "error"; then
    echo "Error al buscar el álbum en Last.fm: $response"
    exit 1
fi
echo "$response"
# Procesa la respuesta JSON para obtener la URL del álbum
album_url=$(echo "$response" | jq -r '.results.albummatches.album[0].url')

# Imprime la URL del álbum
echo "URL del álbum: $album_url"
