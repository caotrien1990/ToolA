# Current ToolA refresh contract

GET [A:API]/api/token/refresh returns the existing token and its original expiry while it is valid. After expiry the handler rotates the token. Calling refresh before expiry does not extend its lifetime. Use the returned expiry and the server response for the current process. Do not schedule periodic refresh as a keepalive.
