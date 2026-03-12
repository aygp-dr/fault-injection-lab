#!/usr/bin/env bash
mkdir -p specs
curl -sL https://raw.githubusercontent.com/PokeAPI/pokeapi/master/openapi.yml \
  -o specs/pokeapi.yaml
echo "Saved specs/pokeapi.yaml ($(wc -l < specs/pokeapi.yaml) lines)"
