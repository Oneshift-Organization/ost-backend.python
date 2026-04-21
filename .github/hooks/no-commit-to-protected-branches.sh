#!/bin/sh
branch=$(git symbolic-ref --short HEAD 2>/dev/null)

if [ "$branch" = "main" ] || [ "$branch" = "develop" ]; then
  echo "❌ Commits diretos em '$branch' não são permitidos."
  echo "   Crie uma branch: feat/<card-linear> ou fix/<card-linear>"
  exit 1
fi
