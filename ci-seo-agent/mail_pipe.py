#!/usr/bin/env python3
"""
Postfix pipe script — called when mail arrives at seo-agent@indogenmed.org.
Configure with: seo-agent: |/home/agents/ci-seo-agent/mail_pipe.py
"""
import sys
import os
sys.path.insert(0, "/home/agents/ci-seo-agent")
os.chdir("/home/agents/ci-seo-agent")

from mail_poller import main_pipe
main_pipe()
