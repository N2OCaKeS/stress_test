#!/bin/sh
set -eu

PORTAINER_BIN="/portainer"
PORTAINER_API_URL="${PORTAINER_API_URL:-http://127.0.0.1:9000}"
STARTUP_TIMEOUT_SECONDS="${PORTAINER_STARTUP_TIMEOUT_SECONDS:-60}"

log() {
  printf '%s\n' "[portainer-init] $*" >&2
}

to_bool_json() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) echo true ;;
    *) echo false ;;
  esac
}

trim_crlf() {
  tr -d '\r\n'
}

is_true() {
  [ "$(to_bool_json "${1:-}")" = "true" ]
}

json_int_or_zero() {
  # Keep JSON numeric types stable for jq --argjson.
  case "${1:-}" in
    ''|null) echo 0 ;;
    *) echo "$1" ;;
  esac
}

"$PORTAINER_BIN" "$@" &
PORTAINER_PID="$!"

term() {
  kill -TERM "$PORTAINER_PID" 2>/dev/null || true
}
trap term INT TERM

ready=false
for _i in $(seq 1 "$STARTUP_TIMEOUT_SECONDS"); do
  # /api/status is deprecated in modern Portainer; prefer /api/system/status.
  if curl -fsS "$PORTAINER_API_URL/api/system/status" >/dev/null 2>&1; then
    ready=true
    break
  fi
  if curl -fsS "$PORTAINER_API_URL/api/status" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  log "Portainer API is not ready after ${STARTUP_TIMEOUT_SECONDS}s; skipping bootstrap."
  wait "$PORTAINER_PID"
  exit $?
fi

ADMIN_USERNAME="${PORTAINER_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${PORTAINER_ADMIN_PASSWORD:-}"

if [ -z "$ADMIN_PASSWORD" ]; then
  log "PORTAINER_ADMIN_PASSWORD is empty; skipping admin/user/oauth bootstrap."
  wait "$PORTAINER_PID"
  exit $?
fi

admin_check_code="$(curl -sS -o /dev/null -w '%{http_code}' "$PORTAINER_API_URL/api/users/admin/check" || true)"
if [ "$admin_check_code" != "204" ]; then
  log "Initializing admin user '${ADMIN_USERNAME}'."
  admin_payload="$(jq -nc --arg u "$ADMIN_USERNAME" --arg p "$ADMIN_PASSWORD" '{username:$u,password:$p}')"
  curl -fsS -X POST "$PORTAINER_API_URL/api/users/admin/init" \
    -H 'Content-Type: application/json' \
    -d "$admin_payload" >/dev/null || true
else
  log "Admin user already exists."
fi

login_payload="$(jq -nc --arg u "$ADMIN_USERNAME" --arg p "$ADMIN_PASSWORD" '{username:$u,password:$p}')"
jwt="$(curl -fsS -X POST "$PORTAINER_API_URL/api/auth" \
  -H 'Content-Type: application/json' \
  -d "$login_payload" | jq -r '.jwt // empty' || true)"

if [ -z "$jwt" ]; then
  log "Failed to log in as '${ADMIN_USERNAME}'. Check PORTAINER_ADMIN_USERNAME/PORTAINER_ADMIN_PASSWORD."
  wait "$PORTAINER_PID"
  exit $?
fi

RBAC_ENABLED="${PORTAINER_RBAC_ENABLED:-true}"
RBAC_TEAM_NAME="${PORTAINER_RBAC_TEAM_NAME:-portainer}"
RBAC_SYNC_USERS="${PORTAINER_RBAC_SYNC_USERS:-true}"
RBAC_SYNC_CONTAINERS="${PORTAINER_RBAC_SYNC_CONTAINERS:-true}"
RBAC_ENDPOINT_NAME="${PORTAINER_RBAC_ENDPOINT_NAME:-local}"

team_id=""
if is_true "$RBAC_ENABLED"; then
  teams_json="$(curl -fsS "$PORTAINER_API_URL/api/teams" -H "Authorization: Bearer $jwt" 2>/dev/null || echo '[]')"
  team_id="$(echo "$teams_json" | jq -r --arg name "$RBAC_TEAM_NAME" '.[] | select(.Name==$name) | .Id' | head -n 1 || true)"

  if [ -z "$team_id" ] || [ "$team_id" = "null" ]; then
    log "Creating Portainer team '${RBAC_TEAM_NAME}'."
    team_payload="$(jq -nc --arg n "$RBAC_TEAM_NAME" '{Name:$n}')"
    team_id="$(curl -fsS -X POST "$PORTAINER_API_URL/api/teams" \
      -H "Authorization: Bearer $jwt" \
      -H 'Content-Type: application/json' \
      -d "$team_payload" | jq -r '.Id // empty' 2>/dev/null || true)"
  else
    log "Using Portainer team '${RBAC_TEAM_NAME}' (id=${team_id})."
  fi
fi

BASE_USERNAME="${PORTAINER_BASE_USERNAME:-}"
BASE_PASSWORD="${PORTAINER_BASE_PASSWORD:-}"
BASE_ROLE="${PORTAINER_BASE_ROLE:-2}"
if [ -n "$BASE_USERNAME" ] && [ -n "$BASE_PASSWORD" ]; then
  log "Ensuring base user '${BASE_USERNAME}'."
  base_payload="$(jq -nc --arg u "$BASE_USERNAME" --arg p "$BASE_PASSWORD" --argjson role "$BASE_ROLE" '{username:$u,password:$p,role:$role}')"
  curl -fsS -X POST "$PORTAINER_API_URL/api/users" \
    -H "Authorization: Bearer $jwt" \
    -H 'Content-Type: application/json' \
    -d "$base_payload" >/dev/null 2>&1 || true
fi

if [ "$(to_bool_json "${PORTAINER_OAUTH_ENABLED:-true}")" = "true" ]; then
  OAUTH_CLIENT_ID="${PORTAINER_OAUTH_CLIENT_ID:-allta-portainer}"

  client_secret="${PORTAINER_OAUTH_CLIENT_SECRET:-}"
  if [ -z "$client_secret" ]; then
    secret_file="${PORTAINER_OAUTH_CLIENT_SECRET_FILE:-/run/secrets/oauth_clients/allta-portainer.secret}"
    if [ -r "$secret_file" ]; then
      client_secret="$(cat "$secret_file" | trim_crlf)"
    fi
  fi

  if [ -z "$client_secret" ]; then
    log "OAuth client secret is missing. Set PORTAINER_OAUTH_CLIENT_SECRET or mount PORTAINER_OAUTH_CLIENT_SECRET_FILE."
  else
    authorization_url="${PORTAINER_OAUTH_AUTHORIZATION_URL:-http://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/authorize}"
    access_token_url="${PORTAINER_OAUTH_ACCESS_TOKEN_URL:-http://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/token}"
    resource_url="${PORTAINER_OAUTH_RESOURCE_URL:-http://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/userinfo}"
    redirect_url="${PORTAINER_OAUTH_REDIRECT_URL:-https://allta.devos.astralinux.ru:9443/}"
    user_identifier="${PORTAINER_OAUTH_USER_IDENTIFIER:-preferred_username}"
    scopes="${PORTAINER_OAUTH_SCOPES:-profile}"
    logout_url="${PORTAINER_OAUTH_LOGOUT_URL:-}"
    sso="$(to_bool_json "${PORTAINER_OAUTH_SSO:-false}")"
    auto_create="$(to_bool_json "${PORTAINER_OAUTH_AUTO_CREATE_USERS:-true}")"
    auth_style="${PORTAINER_OAUTH_AUTH_STYLE:-0}"

    log "Configuring OAuth settings (client_id=${OAUTH_CLIENT_ID})."
    settings="$(curl -fsS "$PORTAINER_API_URL/api/settings" -H "Authorization: Bearer $jwt")"
    patched="$(echo "$settings" | jq \
      --arg client_id "$OAUTH_CLIENT_ID" \
      --arg client_secret "$client_secret" \
      --arg auth "$authorization_url" \
      --arg token "$access_token_url" \
      --arg resource "$resource_url" \
      --arg redirect "$redirect_url" \
      --arg user_identifier "$user_identifier" \
      --arg scopes "$scopes" \
      --arg logout "$logout_url" \
      --argjson sso "$sso" \
      --argjson auto_create "$auto_create" \
      --argjson auth_style "$auth_style" \
      --argjson default_team_id "$(json_int_or_zero "$team_id")" \
      '.AuthenticationMethod=3
       | .OAuthSettings.ClientID=$client_id
       | .OAuthSettings.ClientSecret=$client_secret
       | .OAuthSettings.AuthorizationURI=$auth
       | .OAuthSettings.AccessTokenURI=$token
       | .OAuthSettings.ResourceURI=$resource
       | .OAuthSettings.RedirectURI=$redirect
       | .OAuthSettings.UserIdentifier=$user_identifier
       | .OAuthSettings.Scopes=$scopes
       | .OAuthSettings.LogoutURI=$logout
       | .OAuthSettings.SSO=$sso
       | .OAuthSettings.OAuthAutoCreateUsers=$auto_create
       | (.OAuthSettings.DefaultTeamID=($default_team_id|tonumber) | .)
       | .OAuthSettings.AuthStyle=$auth_style')"

    curl -fsS -X PUT "$PORTAINER_API_URL/api/settings" \
      -H "Authorization: Bearer $jwt" \
      -H 'Content-Type: application/json' \
      -d "$patched" >/dev/null || log "Failed to update Portainer OAuth settings."
  fi
fi

if is_true "$RBAC_ENABLED" && [ -n "$team_id" ] && [ "$team_id" != "null" ] && [ "$team_id" != "0" ]; then
  # Ensure at least one environment exists. This unblocks a fully headless bootstrap on clean volumes.
  endpoints_json="$(curl -fsS "$PORTAINER_API_URL/api/endpoints" -H "Authorization: Bearer $jwt" 2>/dev/null || echo '[]')"
  endpoints_count="$(echo "$endpoints_json" | jq -r 'length' 2>/dev/null || echo 0)"
  if [ "$endpoints_count" = "0" ]; then
    log "No environments found; creating local Docker environment '${RBAC_ENDPOINT_NAME}'."
    curl -fsS -X POST "$PORTAINER_API_URL/api/endpoints?Name=${RBAC_ENDPOINT_NAME}&EndpointCreationType=1" \
      -H "Authorization: Bearer $jwt" >/dev/null 2>&1 || true
    endpoints_json="$(curl -fsS "$PORTAINER_API_URL/api/endpoints" -H "Authorization: Bearer $jwt" 2>/dev/null || echo '[]')"
  fi

  endpoint_id="$(echo "$endpoints_json" | jq -r --arg name "$RBAC_ENDPOINT_NAME" '(.[] | select(.Name==$name) | .Id) // empty' | head -n 1 || true)"
  if [ -z "$endpoint_id" ]; then
    endpoint_id="$(echo "$endpoints_json" | jq -r '.[0].Id // empty' | head -n 1 || true)"
  fi

  if [ -n "$endpoint_id" ]; then
    # Ensure team has access to the environment.
    endpoint_json="$(curl -fsS "$PORTAINER_API_URL/api/endpoints/${endpoint_id}" -H "Authorization: Bearer $jwt" 2>/dev/null || echo '')"
    if [ -n "$endpoint_json" ]; then
      endpoint_patched="$(echo "$endpoint_json" | jq --arg tid "$team_id" '
        .TeamAccessPolicies = (.TeamAccessPolicies // {})
        | .TeamAccessPolicies[$tid] = {"RoleId": 1}
      ' 2>/dev/null || echo '')"
      if [ -n "$endpoint_patched" ]; then
        curl -fsS -X PUT "$PORTAINER_API_URL/api/endpoints/${endpoint_id}" \
          -H "Authorization: Bearer $jwt" \
          -H 'Content-Type: application/json' \
          -d "$endpoint_patched" >/dev/null 2>&1 || true
      fi
    fi

    # Ensure existing regular users are members of the default team (for already-created OAuth users).
    if is_true "$RBAC_SYNC_USERS"; then
      log "Syncing users into team '${RBAC_TEAM_NAME}' (id=${team_id})."
      users_json="$(curl -fsS "$PORTAINER_API_URL/api/users" -H "Authorization: Bearer $jwt" 2>/dev/null || echo '[]')"
      added=0
      for uid in $(echo "$users_json" | jq -r '.[] | select(.Role==2) | .Id' 2>/dev/null || true); do
        membership_payload="$(jq -nc --argjson uid "$uid" --argjson tid "$(json_int_or_zero "$team_id")" '{UserID:$uid,TeamID:$tid,Role:2}')"
        curl -fsS -X POST "$PORTAINER_API_URL/api/team_memberships" \
          -H "Authorization: Bearer $jwt" \
          -H 'Content-Type: application/json' \
          -d "$membership_payload" >/dev/null 2>&1 || true
        added=$((added + 1))
      done
      log "User sync done (attempted=${added})."
    fi

    # Ensure regular users can see/manage existing containers created outside Portainer by creating resource controls.
    if is_true "$RBAC_SYNC_CONTAINERS"; then
      log "Syncing container access controls for team '${RBAC_TEAM_NAME}' (id=${team_id})."
      containers_json="$(curl -fsS "$PORTAINER_API_URL/api/endpoints/${endpoint_id}/docker/containers/json?all=1" \
        -H "Authorization: Bearer $jwt" 2>/dev/null || echo '[]')"
      created=0
      updated=0
      for cid in $(echo "$containers_json" | jq -r '.[].Id' 2>/dev/null || true); do
        inspect_json="$(curl -fsS "$PORTAINER_API_URL/api/endpoints/${endpoint_id}/docker/containers/${cid}/json" \
          -H "Authorization: Bearer $jwt" 2>/dev/null || echo '')"
        [ -n "$inspect_json" ] || continue

        rc_id="$(echo "$inspect_json" | jq -r '.Portainer.ResourceControl.Id // empty' 2>/dev/null || true)"
        tid="$(json_int_or_zero "$team_id")"

        if [ -z "$rc_id" ] || [ "$rc_id" = "null" ]; then
          rc_payload="$(jq -nc --arg rid "$cid" --argjson tid "$tid" '{ResourceID:$rid,Type:1,Teams:[$tid]}')"
          curl -fsS -X POST "$PORTAINER_API_URL/api/resource_controls" \
            -H "Authorization: Bearer $jwt" \
            -H 'Content-Type: application/json' \
            -d "$rc_payload" >/dev/null 2>&1 || true
          created=$((created + 1))
          continue
        fi

        # If the resource control exists but doesn't grant access to our team, add it via PUT.
        if echo "$inspect_json" | jq -e --argjson tid "$tid" '(.Portainer.ResourceControl.TeamAccesses // []) | any(.TeamId==$tid)' >/dev/null 2>&1; then
          continue
        fi

        teams="$(echo "$inspect_json" | jq -c '(.Portainer.ResourceControl.TeamAccesses // []) | map(.TeamId)' 2>/dev/null || echo '[]')"
        users="$(echo "$inspect_json" | jq -c '(.Portainer.ResourceControl.UserAccesses // []) | map(.UserId)' 2>/dev/null || echo '[]')"
        sub="$(echo "$inspect_json" | jq -c '(.Portainer.ResourceControl.SubResourceIds // [])' 2>/dev/null || echo '[]')"
        public="$(echo "$inspect_json" | jq -r '(.Portainer.ResourceControl.Public // false)' 2>/dev/null || echo 'false')"

        update_payload="$(jq -nc \
          --argjson teams "$teams" \
          --argjson users "$users" \
          --argjson sub "$sub" \
          --argjson tid "$tid" \
          --argjson public "$public" \
          '{AdministratorsOnly:false,Public:$public,Users:$users,Teams:($teams + [$tid] | unique),SubResourceIDs:$sub}')"
        curl -fsS -X PUT "$PORTAINER_API_URL/api/resource_controls/${rc_id}" \
          -H "Authorization: Bearer $jwt" \
          -H 'Content-Type: application/json' \
          -d "$update_payload" >/dev/null 2>&1 || true
        updated=$((updated + 1))
      done
      log "Container access sync done (created=${created}, updated=${updated})."
    fi
  fi
fi

wait "$PORTAINER_PID"
