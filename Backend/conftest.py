"""Empty on purpose — its only job is to mark Backend/ as pytest's rootdir,
so `from Model.x import Y` / `from Service.x import Y` style imports (used
throughout this codebase) resolve the same way under pytest as they do when
running the app normally from this directory.
"""
