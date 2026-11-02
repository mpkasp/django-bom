"""Organization-scoped operations shared by every surface (HTML views, MCP server, future API).

Services take an organization (and a user when permissions matter), return model instances or
plain dicts, and never touch a request. Writes go through the existing forms so validation,
immutability rules, and part-number assignment have a single implementation.
"""
