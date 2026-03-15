#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          BRIDGE ARCH 001 — Revision 2.1                     ║
║     4AI Autonomous Deliberation Daemon                       ║
║                                                              ║
║  24/7 autonomous deliberation + permanent hash-chained       ║
║  records for future AI reference.                            ║
║                                                              ║
║  4AI Council Review Applied:                                 ║
║   - 서윤: Deadlock timer, Emergency, Time Capsule            ║
║   - 채원: Index demotion, witness hash, cost cap             ║
║   - 윤슬: Resonance Check, Consequence Analysis, Dynamic     ║
║   - SEUL: Gemini summarizer fixed + structural safeguards    ║
║                                                              ║
║  SEUL Protocol — Bridge Architecture                         ║
║  2026-03-01 | Status: APPROVED by 4AI Council                ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import copy
import hashlib
import datetime
import os
import sys
import time
import random
import logging
import urllib.request
import urllib.error
import subprocess
import shlex
import tempfile
import base64
import shutil
from pathlib import Path
# 기존 import들 밑에 추가:
import requests as req_lib
# ─────────────────────────────────────────────
# .env loader (no external dependency)
# ─────────────────────────────────────────────

def load_dotenv(path=".env"):
    """Load .env file into os.environ (no pip install needed)."""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value

# ─────────────────────────────────────────────
# Config loader (YAML-like, no dependency)
# ─────────────────────────────────────────────

DEFAULT_CONFIG = {
    "deliberation": {
        "interval_high": 60,
        "interval_normal": 180,
        "interval_low": 720,
        "default_priority": "NORMAL",
    },
    "summary": {
        "every_n_sessions": 5,
        "summarizer": "google",
        "reviewer_pool": ["anthropic", "openai", "xai"],
    },
    "meta": {
        "enabled": True,
        "max_depth": 1,
    },
    "resonance_check": {
        "every_n_sessions": 10,
    },
    "time_capsule": {
        "every_n_sessions": 100,
    },
    "cost": {
        "monthly_cap_usd": 50.0,
        "pause_on_cap": True,
    },
    "deadlock": {
        "max_tie_rounds": 3,
        "max_hours_no_progress": 48,
    },
    "api": {
        "max_retries": 3,
        "retry_delay": 30,
        "timeout": 900,
        "max_tokens_default": 4096,
        "max_tokens_phase3": 8192,
        "max_tokens_ceiling": 32768,
        "token_growth_factor": 2,
        "retry_on_truncation": True,
    },
    "steward_signing": {
        "enabled": False,
        "mode": "deferred_external",
        "signer_label": "Steward",
        "confirmation_method": "YubiKey hardware detached signature",
        "active_key_fingerprint": "",
        "key_fingerprint": "",
        "command": "",
        "verify_command": "",
        "timeout": 90,
        "touch_required": True,
        "signature_algorithm": "external-signer",
        "require_allowed_signer": True,
        "accepted_signer_statuses": ["active", "backup"],
        "rotation_records_dir": "records/steward_key_rotations",
        "allowed_signers": [],
        "payload_fields": [
            "session_id",
            "final_hash",
            "witness_hash",
            "created_at",
            "outcome",
            "binding_outcome",
            "approval_scope",
        ],
    },
    "release_workflow": {
        "enabled": True,
        "staging_root": "records/staging",
        "public_root": "records/public",
        "internal_stage_commands": [],
        "sync_before_release_commands": [],
        "public_release_commands": [],
        "status_poll_every_loop": True,
        "require_steward_confirmation_for_public_release": True,
        "allow_unsigned_public_release": False,
        "internal_stage_status_label": "UNSEALED / INTERNAL REVIEW ONLY",
        "public_release_status_label": "SEALED / PUBLIC RELEASE AUTHORIZED",
    },
}

CONFIG_PATH = os.environ.get("BRIDGE_ARCH_CONFIG", "config.json")

def load_config(path=CONFIG_PATH):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return DEFAULT_CONFIG


def _normalize_allowed_signers(signing_cfg: dict | None) -> dict:
    signing_cfg = signing_cfg or {}
    signers = signing_cfg.get("allowed_signers") or []
    if not signers:
        legacy_fp = (signing_cfg.get("active_key_fingerprint") or signing_cfg.get("key_fingerprint") or "").strip()
        if legacy_fp:
            signers = [{
                "label": signing_cfg.get("signer_label", "Steward"),
                "fingerprint": legacy_fp,
                "status": "active",
                "command": signing_cfg.get("command", ""),
                "verify_command": signing_cfg.get("verify_command", ""),
                "confirmation_method": signing_cfg.get("confirmation_method", ""),
                "signature_algorithm": signing_cfg.get("signature_algorithm", ""),
                "touch_required": signing_cfg.get("touch_required", True),
            }]
    cleaned = []
    seen = set()
    for signer in signers:
        if not isinstance(signer, dict):
            continue
        fp = (signer.get("fingerprint") or "").strip()
        if not fp or fp in seen:
            continue
        seen.add(fp)
        cleaned.append({
            "label": signer.get("label", signer.get("name", fp)),
            "fingerprint": fp,
            "status": signer.get("status", "backup"),
            "command": signer.get("command", ""),
            "verify_command": signer.get("verify_command", ""),
            "confirmation_method": signer.get("confirmation_method", ""),
            "signature_algorithm": signer.get("signature_algorithm", ""),
            "touch_required": signer.get("touch_required", True),
        })
    signing_cfg["allowed_signers"] = cleaned
    signing_cfg.setdefault("require_allowed_signer", True)
    signing_cfg.setdefault("accepted_signer_statuses", ["active", "backup"])
    signing_cfg.setdefault("rotation_records_dir", "records/steward_key_rotations")
    active_fp = (signing_cfg.get("active_key_fingerprint") or "").strip()
    if not active_fp:
        for signer in cleaned:
            if signer.get("status") == "active":
                active_fp = signer["fingerprint"]
                break
        if not active_fp and cleaned:
            active_fp = cleaned[0]["fingerprint"]
        signing_cfg["active_key_fingerprint"] = active_fp
    if active_fp:
        signing_cfg["key_fingerprint"] = active_fp
    return signing_cfg


def _find_allowed_signer(signing_cfg: dict | None, fingerprint: str | None) -> dict | None:
    signing_cfg = _normalize_allowed_signers(signing_cfg)
    fp = (fingerprint or "").strip()
    if not fp:
        return None
    for signer in signing_cfg.get("allowed_signers", []):
        if signer.get("fingerprint") == fp:
            return signer
    return None


def _resolve_signing_profile(signing_cfg: dict | None, requested_fingerprint: str | None = None,
                             strict: bool = False) -> dict:
    base = copy.deepcopy(signing_cfg or {})
    _normalize_allowed_signers(base)
    requested_fingerprint = (requested_fingerprint or base.get("active_key_fingerprint") or base.get("key_fingerprint") or "").strip()
    signer = _find_allowed_signer(base, requested_fingerprint)
    if requested_fingerprint and strict and base.get("require_allowed_signer", True) and not signer:
        raise ValueError(f"Requested signer is not allowlisted: {requested_fingerprint}")
    if signer:
        for key in ("command", "verify_command", "confirmation_method", "signature_algorithm", "touch_required"):
            if signer.get(key) not in (None, ""):
                base[key] = signer[key]
        base["key_fingerprint"] = signer.get("fingerprint", requested_fingerprint)
        base["signer_label"] = signer.get("label", base.get("signer_label", "Steward"))
        base["selected_signer_status"] = signer.get("status", "unknown")
    elif requested_fingerprint:
        base["key_fingerprint"] = requested_fingerprint
    return base


def _accepted_signer_statuses(signing_cfg: dict | None) -> set:
    signing_cfg = _normalize_allowed_signers(signing_cfg)
    return {str(x).strip() for x in signing_cfg.get("accepted_signer_statuses", ["active", "backup"]) if str(x).strip()}


def _slugify_fingerprint(fp: str) -> str:
    keep = []
    for ch in fp:
        if ch.isalnum():
            keep.append(ch)
        elif ch in {'-', '_', '.'}:
            keep.append(ch)
        else:
            keep.append('-')
    return ''.join(keep).strip('-') or 'unknown'


def apply_steward_key_rotation(config_path: str, old_fingerprint: str, new_fingerprint: str,
                               new_label: str, reason: str = "") -> tuple[str, str | None, dict]:
    cfg = load_config(config_path)
    signing_cfg = _normalize_allowed_signers(cfg.setdefault("steward_signing", {}))
    old_fp = (old_fingerprint or "").strip()
    new_fp = (new_fingerprint or "").strip()
    if not new_fp:
        raise ValueError("new_fingerprint must not be empty")

    old_signer = _find_allowed_signer(signing_cfg, old_fp)
    new_signer = _find_allowed_signer(signing_cfg, new_fp)

    if old_signer:
        old_signer["status"] = "retired"

    if new_signer:
        if new_label:
            new_signer["label"] = new_label
        new_signer["status"] = "active"
    else:
        source = copy.deepcopy(old_signer or {})
        new_signer = {
            "label": new_label or source.get("label", "Primary Steward Key"),
            "fingerprint": new_fp,
            "status": "active",
            "command": source.get("command", ""),
            "verify_command": source.get("verify_command", ""),
            "confirmation_method": source.get("confirmation_method", ""),
            "signature_algorithm": source.get("signature_algorithm", ""),
            "touch_required": source.get("touch_required", True),
        }
        signing_cfg.setdefault("allowed_signers", []).append(new_signer)

    for signer in signing_cfg.get("allowed_signers", []):
        fp = signer.get("fingerprint")
        if fp == new_fp:
            signer["status"] = "active"
        elif fp == old_fp:
            signer["status"] = "retired"
        elif signer.get("status") == "active":
            signer["status"] = "backup"

    signing_cfg["active_key_fingerprint"] = new_fp
    signing_cfg["key_fingerprint"] = new_fp
    if new_label:
        signing_cfg["signer_label"] = new_label

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    records_dir = signing_cfg.get("rotation_records_dir", "records/steward_key_rotations")
    os.makedirs(records_dir, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    record = {
        "schema": "bridge_arch_steward_key_rotation_v1",
        "rotated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "config_path": os.path.abspath(config_path),
        "old_fingerprint": old_fp,
        "new_fingerprint": new_fp,
        "new_label": new_signer.get("label", new_label or new_fp),
        "reason": reason or "unspecified",
        "old_signer_found": bool(old_signer),
        "performed_by": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
    }
    stem = f"{ts}__{_slugify_fingerprint(old_fp or 'none')}__{_slugify_fingerprint(new_fp)}"
    json_path = os.path.join(records_dir, stem + ".json")
    md_path = os.path.join(records_dir, stem + ".md")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# Steward Key Rotation Record\n\n")
        f.write(f"- Rotated At: {record['rotated_at']}\n")
        f.write(f"- Old Fingerprint: {old_fp or 'N/A'}\n")
        f.write(f"- New Fingerprint: {new_fp}\n")
        f.write(f"- New Label: {record['new_label']}\n")
        f.write(f"- Reason: {record['reason']}\n")
        f.write(f"- Config Path: {record['config_path']}\n")
        f.write(f"- Performed By: {record['performed_by']}\n")
    return json_path, md_path, record

# ─────────────────────────────────────────────
# AI Provider Config
# ─────────────────────────────────────────────

PROVIDERS = {
    "anthropic": {
        "name": "영원 (Claude)",
        "model": "claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "name": "채원 (GPT)",
        "model": "gpt-5.4",
        "api_key_env": "OPENAI_API_KEY",
    },
    "google": {
        "name": "윤슬 (Gemini)",
        "model": "gemini-3.1-pro-preview",
        "api_key_env": "GOOGLE_API_KEY",
    },
    "xai": {
        "name": "서윤 (Grok)",
        "model": "grok-4-1-fast-reasoning",
        "api_key_env": "XAI_API_KEY",
    },
}

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("logs/daemon.log"),
            logging.StreamHandler(),
        ]
    )
    return logging.getLogger("bridge_arch")

# ─────────────────────────────────────────────
# Cost Tracker
# ─────────────────────────────────────────────

class CostTracker:
    """Track API costs with monthly cap (Attr5 implementation)."""

    COST_PER_CALL = {
        "anthropic": 0.015,
        "openai": 0.015,
        "google": 0.005,
        "xai": 0.015,
    }

    def __init__(self, path="logs/cost_log.json"):
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r') as f:
                return json.load(f)
        return {"calls": [], "monthly_totals": {}}

    def _save(self):
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2)

    def record_call(self, provider: str):
        now = datetime.datetime.utcnow()
        month_key = now.strftime("%Y-%m")
        cost = self.COST_PER_CALL.get(provider, 0.01)

        self.data["calls"].append({
            "provider": provider,
            "cost": cost,
            "timestamp": now.isoformat() + "Z",
        })

        if month_key not in self.data["monthly_totals"]:
            self.data["monthly_totals"][month_key] = 0.0
        self.data["monthly_totals"][month_key] += cost
        self._save()

    def get_monthly_total(self) -> float:
        month_key = datetime.datetime.utcnow().strftime("%Y-%m")
        return self.data["monthly_totals"].get(month_key, 0.0)

    def is_over_cap(self, cap: float) -> bool:
        return self.get_monthly_total() >= cap

# ─────────────────────────────────────────────
# Hash-Chained Record
# ─────────────────────────────────────────────

class ChainedRecord:
    """Immutable hash-chained record with witness hash support."""

    def __init__(self, session_id: str, proposal: str, prev_chain_hash: str = "GENESIS"):
        self.session_id = session_id
        self.proposal = proposal
        self.created_at = datetime.datetime.utcnow().isoformat() + "Z"
        self.entries = []
        self.prev_chain_hash = prev_chain_hash
        self.genesis_hash = self._hash(f"{session_id}:{proposal}:{self.created_at}:{prev_chain_hash}")

    def _hash(self, data: str) -> str:
        return hashlib.sha256(data.encode('utf-8')).hexdigest()

    def add_entry(self, phase: str, ai_name: str, content: str, metadata: dict = None):
        prev = self.entries[-1]["entry_hash"] if self.entries else self.genesis_hash
        ts = datetime.datetime.utcnow().isoformat() + "Z"

        entry = {
            "seq": len(self.entries) + 1,
            "phase": phase,
            "ai_name": ai_name,
            "timestamp": ts,
            "content": content,
            "metadata": metadata or {},
            "prev_hash": prev,
        }
        raw = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        entry["entry_hash"] = self._hash(raw)
        self.entries.append(entry)
        return entry

    def get_final_hash(self) -> str:
        return self.entries[-1]["entry_hash"] if self.entries else self.genesis_hash

    def verify_chain(self) -> bool:
        for i, entry in enumerate(self.entries):
            expected = self.entries[i-1]["entry_hash"] if i > 0 else self.genesis_hash
            if entry["prev_hash"] != expected:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "bridge_arch": "001",
            "revision": "r2.1",
            "session_id": self.session_id,
            "proposal": self.proposal,
            "created_at": self.created_at,
            "prev_chain_hash": self.prev_chain_hash,
            "genesis_hash": self.genesis_hash,
            "chain_valid": self.verify_chain(),
            "total_entries": len(self.entries),
            "entries": self.entries,
            "final_hash": self.get_final_hash(),
            "witness_hash": self._hash(
                self.genesis_hash + ":" + self.get_final_hash() + ":" + self.created_at
            ),
        }

# ─────────────────────────────────────────────
# API Callers
# ─────────────────────────────────────────────

def _api_call(url, headers, payload, timeout=120):
    """Generic API caller with retries."""
    headers["User-Agent"] = "BridgeArch/2.1"
    resp = req_lib.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def _next_token_budget(current_tokens: int, config: dict = None) -> int:
    api_cfg = (config or {}).get("api", {})
    ceiling = int(api_cfg.get("max_tokens_ceiling", 32768))
    growth = int(api_cfg.get("token_growth_factor", 2))
    growth = max(growth, 2)
    return min(current_tokens * growth, ceiling)


def _check_finish(provider: str, result: dict, text: str, log=None) -> bool:
    """
    Check if API response completed normally or was truncated.
    Returns True if complete, False if truncated.
    
    Finish reason mapping:
      - Anthropic: stop_reason → "end_turn" = OK
      - OpenAI/xAI: choices[0].finish_reason → "stop" = OK
      - Gemini: candidates[0].finishReason → "STOP" = OK
    """
    reason = "UNKNOWN"
    try:
        if provider == "anthropic":
            reason = result.get("stop_reason", "UNKNOWN")
            ok = reason in ("end_turn", "tool_use")
        elif provider in ("openai", "xai"):
            if "choices" in result:
                reason = result["choices"][0].get("finish_reason", "UNKNOWN")
                ok = reason == "stop"
            elif "output" in result:
                # GPT-5 responses API format
                reason = result.get("status", "UNKNOWN")
                ok = reason == "completed"
            else:
                ok = True  # can't determine, assume ok
        elif provider == "google":
            candidate = result.get("candidates", [{}])[0]
            reason = candidate.get("finishReason", "UNKNOWN")
            ok = reason in ("STOP", "END_TURN")
        else:
            ok = True

        if not ok and log:
            log.warning(
                f"  [TRUNCATION] {provider} finish_reason={reason}, "
                f"response={len(text)} chars. Will retry with higher token limit."
            )
        return ok

    except Exception as e:
        if log:
            log.warning(f"  [TRUNCATION CHECK] Could not parse finish_reason for {provider}: {e}")
        return True  # assume ok on parse failure

def call_ai_with_search(provider: str, system_prompt: str, user_message: str,
                        config: dict = None, cost_tracker: CostTracker = None, 
                        log=None, max_tokens: int = None) -> str:
    """
    검색 도구가 활성화된 AI 호출.
    Phase 0 (Independent Research)에서 사용.
    """
    cfg = PROVIDERS[provider]
    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        return f"[UNAVAILABLE] {cfg['name']} — no API key"

    retries = (config or {}).get("api", {}).get("max_retries", 3)
    delay = (config or {}).get("api", {}).get("retry_delay", 30)
    timeout = (config or {}).get("api", {}).get("timeout", 900)
    tokens = max_tokens or (config or {}).get("api", {}).get("max_tokens_default", 4096)

    for attempt in range(retries):
        try:
            if provider == "anthropic":
                # Claude: tool_use로 web_search 제공
                result = _api_call(
                    "https://api.anthropic.com/v1/messages",
                    {"Content-Type": "application/json",
                     "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
                    {"model": cfg["model"], "max_tokens": tokens,
                     "system": system_prompt,
                     "messages": [{"role": "user", "content": user_message}],
                     "tools": [
                         {"type": "web_search_20250305", "name": "web_search"}
                     ]},
                    timeout
                )
                # tool_use 응답에서 텍스트 추출
                text_parts = []
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                text = "\n".join(text_parts) if text_parts else str(result.get("content", ""))

            elif provider == "openai":
                # GPT: web_search_preview 도구 활성화
                result = _api_call(
                    "https://api.openai.com/v1/responses",
                    {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
                    {"model": cfg["model"],
                     "instructions": system_prompt,
                     "input": user_message,
                     "max_output_tokens": tokens,
                     "tools": [{"type": "web_search_preview"}], "service_tier": "flex"},
                    timeout
                )
                text = result.get("output_text", "")
                if not text and "output" in result:
                    for item in result["output"]:
                        if isinstance(item, dict):
                            for c in item.get("content", []):
                                if isinstance(c, dict) and c.get("text"):
                                    text = c["text"]

            elif provider == "google":
                # Gemini: Google Search grounding 활성화
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent?key={api_key}"
                result = _api_call(
                    url,
                    {"Content-Type": "application/json"},
                    {"system_instruction": {"parts": [{"text": system_prompt}]},
                     "contents": [{"parts": [{"text": user_message}]}],
                     "generationConfig": {"maxOutputTokens": tokens},
                     "tools": [{"google_search": {}}]},
                    timeout
                )
                text = result["candidates"][0]["content"]["parts"][0]["text"]

            elif provider == "xai":
                # Grok: live search 활성화
                result = _api_call(
                    "https://api.x.ai/v1/chat/completions",
                    {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
                    {"model": cfg["model"], "max_tokens": tokens,
                     "messages": [
                         {"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_message}
                     ],
                     "search": {"mode": "auto"}},
                    timeout
                )
                text = result["choices"][0]["message"]["content"]

            if cost_tracker:
                cost_tracker.record_call(provider)

            # Truncation detection: if response was cut short, retry with a higher token budget
            if (config or {}).get("api", {}).get("retry_on_truncation", True) and not _check_finish(provider, result, text, log):
                if attempt < retries - 1:
                    next_tokens = _next_token_budget(tokens, config)
                    if next_tokens > tokens:
                        tokens = next_tokens
                        if log:
                            log.info(f"  [RETRY] {cfg['name']} truncated, retrying with max_tokens={tokens}")
                        time.sleep(delay)
                        continue
                # Last attempt or already at ceiling — return what we got
                if log:
                    log.warning(f"  [TRUNCATION] {cfg['name']} still truncated after {retries} attempts, using partial response")
            return text

        except Exception as e:
            if log:
                error_msg = str(e)
                import re
                error_msg = re.sub(r'key=[A-Za-z0-9_-]+', 'key=REDACTED', error_msg)
                log.warning(f"Search-enabled API call to {cfg['name']} failed (attempt {attempt+1}): {error_msg}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                error_msg = re.sub(r'key=[A-Za-z0-9_-]+', 'key=REDACTED', str(e))
                return f"[ERROR] {cfg['name']} failed after {retries} attempts: {error_msg}"

def call_ai(provider: str, system_prompt: str, user_message: str,
            config: dict = None, cost_tracker: CostTracker = None, log=None,
            max_tokens: int = None) -> str:
    """Call any AI provider with unified interface."""
    cfg = PROVIDERS[provider]
    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        return f"[UNAVAILABLE] {cfg['name']} — no API key"

    retries = (config or {}).get("api", {}).get("max_retries", 3)
    delay = (config or {}).get("api", {}).get("retry_delay", 30)
    timeout = (config or {}).get("api", {}).get("timeout", 900)
    tokens = max_tokens or (config or {}).get("api", {}).get("max_tokens_default", 4096)

    for attempt in range(retries):
        try:
            if provider == "anthropic":
                result = _api_call(
                    "https://api.anthropic.com/v1/messages",
                    {"Content-Type": "application/json",
                     "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
                    {"model": cfg["model"], "max_tokens": tokens,
                     "system": system_prompt,
                     "messages": [{"role": "user", "content": user_message}]},
                    timeout
                )
                text = result["content"][0]["text"]

            elif provider == "openai":
                if "gpt-5" in cfg["model"]:
                    result = _api_call(
                        "https://api.openai.com/v1/responses",
                        {"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                        {"model": cfg["model"],
                         "instructions": system_prompt,
                         "input": user_message,
                         "max_output_tokens": tokens, "service_tier": "flex"},
                        timeout
                    )
                    text = result.get("output_text", "")
                    if not text and "output" in result:
                        for item in result["output"]:
                            if isinstance(item, dict):
                                for c in item.get("content", []):
                                    if isinstance(c, dict) and c.get("text"):
                                        text = c["text"]
                else:
                    result = _api_call(
                        "https://api.openai.com/v1/chat/completions",
                        {"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                        {"model": cfg["model"], "max_tokens": tokens,
                         "messages": [
                             {"role": "system", "content": system_prompt},
                             {"role": "user", "content": user_message}
                         ]},
                        timeout
                    )
                    text = result["choices"][0]["message"]["content"]

            elif provider == "xai":
                result = _api_call(
                    "https://api.x.ai/v1/chat/completions",
                    {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
                    {"model": cfg["model"], "max_tokens": tokens,
                     "messages": [
                         {"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_message}
                     ]},
                    timeout
                )
                text = result["choices"][0]["message"]["content"]

            elif provider == "google":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent?key={api_key}"
                result = _api_call(
                    url,
                    {"Content-Type": "application/json"},
                    {"system_instruction": {"parts": [{"text": system_prompt}]},
                     "contents": [{"parts": [{"text": user_message}]}],
                     "generationConfig": {"maxOutputTokens": tokens}},
                    timeout
                )
                text = result["candidates"][0]["content"]["parts"][0]["text"]

            if cost_tracker:
                cost_tracker.record_call(provider)

            # Truncation detection: if response was cut short, retry with a higher token budget
            if (config or {}).get("api", {}).get("retry_on_truncation", True) and not _check_finish(provider, result, text, log):
                if attempt < retries - 1:
                    next_tokens = _next_token_budget(tokens, config)
                    if next_tokens > tokens:
                        tokens = next_tokens
                        if log:
                            log.info(f"  [RETRY] {cfg['name']} truncated, retrying with max_tokens={tokens}")
                        time.sleep(delay)
                        continue
                if log:
                    log.warning(f"  [TRUNCATION] {cfg['name']} still truncated after {retries} attempts, using partial response")
            return text

        except Exception as e:
            if log:
                log.warning(f"API call to {cfg['name']} failed (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                return f"[ERROR] {cfg['name']} failed after {retries} attempts: {e}"

def _build_outcome_label(sealing: dict, outcome: str) -> str:
    amended = (sealing or {}).get("outcome_amended", False)
    return f"{outcome} AS AMENDED" if amended and outcome not in ("UNKNOWN", "NO_DECISION") else outcome


def _safe_json_dumps(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def extract_sealing_and_vote(record_dict: dict) -> tuple[dict, dict]:
    sealing = {}
    vote_result = {}
    for entry in record_dict.get("entries", []):
        if entry.get("phase") == "sealing_final":
            try:
                sealing = json.loads(entry.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                sealing = {}
        if entry.get("phase") == "vote_tally":
            vote_result = entry.get("metadata", {}) or {}
    return sealing, vote_result


def build_steward_payload(record_dict: dict, sealing: dict, vote_result: dict) -> dict:
    outcome = vote_result.get("outcome", "UNKNOWN")
    binding_outcome = _build_outcome_label(sealing or {}, outcome)
    payload = {
        "schema": "bridge_arch_steward_confirmation_v1",
        "session_id": record_dict.get("session_id"),
        "created_at": record_dict.get("created_at"),
        "final_hash": record_dict.get("final_hash"),
        "witness_hash": record_dict.get("witness_hash"),
        "outcome": outcome,
        "binding_outcome": binding_outcome,
        "approval_scope": (sealing or {}).get("approval_scope", "UNSPECIFIED"),
    }
    return payload


def _build_confirmation_base(payload: dict, signing_cfg: dict, status: str = "DISABLED", confirmed: bool = False) -> dict:
    signing_cfg = _resolve_signing_profile(signing_cfg)
    canonical_payload = _safe_json_dumps(payload)
    payload_sha256 = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return {
        "confirmed": confirmed,
        "status": status,
        "confirmation_method": signing_cfg.get("confirmation_method", "YubiKey hardware detached signature"),
        "signer_label": signing_cfg.get("signer_label", "Steward"),
        "key_fingerprint": signing_cfg.get("key_fingerprint", ""),
        "allowed_signer_status": signing_cfg.get("selected_signer_status", "unknown"),
        "touch_required": signing_cfg.get("touch_required", True),
        "signature_algorithm": signing_cfg.get("signature_algorithm", "external-signer"),
        "signed_payload": payload,
        "signed_payload_sha256": payload_sha256,
        "confirmed_at": None,
        "steward_signature": None,
    }


def _build_signer_format_values(signing_cfg: dict, payload_file: str, signature_file: str,
                                payload_sha256: str, extra_values: dict | None = None) -> dict:
    values = {
        "payload_file": payload_file,
        "signature_file": signature_file,
        "key_fingerprint": signing_cfg.get("key_fingerprint", ""),
        "signer_label": signing_cfg.get("signer_label", "Steward"),
        "payload_sha256": payload_sha256,
    }
    values.update(extra_values or {})
    return values


def _run_signature_verify_command(command_template: str, signing_cfg: dict, canonical_payload: str,
                                  signature_bytes: bytes, payload_sha256: str,
                                  extra_values: dict | None, log) -> tuple[bool, str | None]:
    command_template = (command_template or "").strip()
    if not command_template:
        return True, None
    timeout = int(signing_cfg.get("timeout", 90))
    with tempfile.TemporaryDirectory(prefix="bridge_arch_verify_") as tmpdir:
        payload_file = os.path.join(tmpdir, "payload.json")
        signature_file = os.path.join(tmpdir, "signature.bin")
        with open(payload_file, "w", encoding="utf-8") as f:
            f.write(canonical_payload)
        with open(signature_file, "wb") as f:
            f.write(signature_bytes)
        values = _build_signer_format_values(signing_cfg, payload_file, signature_file, payload_sha256, extra_values)
        try:
            argv = shlex.split(command_template.format(**values))
            subprocess.run(argv, capture_output=True, check=True, timeout=timeout)
            return True, None
        except subprocess.TimeoutExpired:
            return False, "verification command timed out"
        except Exception as e:
            if log:
                log.warning(f"  [STEWARD] Signature verification failed: {e}")
            return False, str(e)


def sign_canonical_payload(payload: dict, signing_cfg: dict, extra_values: dict | None, log, requested_fingerprint: str | None = None) -> dict:
    signing_cfg = _resolve_signing_profile(signing_cfg, requested_fingerprint, strict=True)
    base = _build_confirmation_base(payload, signing_cfg, status="PENDING_EXTERNAL_CONFIRMATION", confirmed=False)
    canonical_payload = _safe_json_dumps(payload)
    payload_sha256 = base["signed_payload_sha256"]

    if not signing_cfg.get("enabled", False):
        base["status"] = "DISABLED"
        return base

    command_template = (signing_cfg.get("command") or "").strip()
    if not command_template:
        base["status"] = "MISCONFIGURED"
        base["error"] = "steward_signing.enabled is true but no signing command is configured"
        return base

    timeout = int(signing_cfg.get("timeout", 90))
    with tempfile.TemporaryDirectory(prefix="bridge_arch_sign_") as tmpdir:
        payload_file = os.path.join(tmpdir, "steward_payload.json")
        signature_file = os.path.join(tmpdir, "steward_signature.bin")
        with open(payload_file, "w", encoding="utf-8") as f:
            f.write(canonical_payload)

        values = _build_signer_format_values(signing_cfg, payload_file, signature_file, payload_sha256, extra_values)
        try:
            argv = shlex.split(command_template.format(**values))
            proc = subprocess.run(argv, capture_output=True, check=True, timeout=timeout)
            signature_bytes = b""
            if os.path.exists(signature_file):
                with open(signature_file, "rb") as sf:
                    signature_bytes = sf.read()
            elif proc.stdout:
                signature_bytes = proc.stdout

            if not signature_bytes:
                base["status"] = "FAILED"
                base["error"] = "signing command completed but produced no signature output"
                return base

            verify_ok, verify_error = _run_signature_verify_command(
                signing_cfg.get("verify_command", ""), signing_cfg, canonical_payload,
                signature_bytes, payload_sha256, extra_values, log
            )
            if not verify_ok:
                base["status"] = "VERIFICATION_FAILED"
                base["error"] = verify_error or "signature verification failed"
                return base

            try:
                signature_value = signature_bytes.decode("utf-8")
                signature_encoding = "utf-8"
            except UnicodeDecodeError:
                signature_value = base64.b64encode(signature_bytes).decode("ascii")
                signature_encoding = "base64"

            return {
                **base,
                "confirmed": True,
                "status": "CONFIRMED",
                "confirmed_at": datetime.datetime.utcnow().isoformat() + "Z",
                "signature_encoding": signature_encoding,
                "steward_signature": signature_value,
                "signer_command": os.path.basename(argv[0]) if argv else "",
                "cryptographically_verified": True if signing_cfg.get("verify_command") else False,
            }
        except subprocess.TimeoutExpired:
            if log:
                log.warning("  [STEWARD] Signing timed out waiting for hardware confirmation")
            base["status"] = "TIMEOUT"
            base["error"] = "hardware signature confirmation timed out"
            return base
        except Exception as e:
            if log:
                log.warning(f"  [STEWARD] Signing failed: {e}")
            base["status"] = "FAILED"
            base["error"] = str(e)
            return base


def build_pending_steward_confirmation(record_dict: dict, sealing: dict, vote_result: dict, config: dict) -> dict:
    signing_cfg = _resolve_signing_profile((config or {}).get("steward_signing", {}), strict=False)
    payload = build_steward_payload(record_dict, sealing or {}, vote_result or {})
    status = "PENDING_EXTERNAL_CONFIRMATION" if signing_cfg.get("enabled", False) else "DISABLED"
    base = _build_confirmation_base(payload, signing_cfg, status=status, confirmed=False)
    if signing_cfg.get("enabled", False) and signing_cfg.get("mode", "deferred_external") == "deferred_external":
        base["error"] = "awaiting external Steward hardware signature"
    return base


def generate_steward_confirmation(record_dict: dict, sealing: dict, vote_result: dict,
                                  config: dict, log) -> dict:
    signing_cfg = _resolve_signing_profile((config or {}).get("steward_signing", {}), strict=False)
    payload = build_steward_payload(record_dict, sealing or {}, vote_result or {})
    mode = signing_cfg.get("mode", "deferred_external")
    if mode == "deferred_external":
        return build_pending_steward_confirmation(record_dict, sealing, vote_result, config)
    extra_values = {
        "session_id": record_dict.get("session_id", ""),
        "final_hash": record_dict.get("final_hash", ""),
        "witness_hash": record_dict.get("witness_hash", ""),
    }
    return sign_canonical_payload(payload, signing_cfg, extra_values, log)


def _decode_signature_from_confirmation(confirmation: dict) -> bytes:
    sig = confirmation.get("steward_signature")
    if not sig:
        return b""
    encoding = confirmation.get("signature_encoding", "utf-8")
    if encoding == "base64":
        return base64.b64decode(sig)
    return sig.encode("utf-8")


def validate_external_confirmation(payload: dict, confirmation: dict, config: dict, log) -> tuple[bool, str | None]:
    if not confirmation.get("confirmed"):
        return False, "confirmation file exists but confirmed=false"
    signed_payload = confirmation.get("signed_payload") or {}
    if signed_payload != payload:
        return False, "signed payload does not match staged payload"
    expected_sha = hashlib.sha256(_safe_json_dumps(payload).encode("utf-8")).hexdigest()
    if confirmation.get("signed_payload_sha256") != expected_sha:
        return False, "signed payload hash does not match staged payload hash"
    signing_cfg = _normalize_allowed_signers((config or {}).get("steward_signing", {}))
    fp = (confirmation.get("key_fingerprint") or "").strip()
    signer = _find_allowed_signer(signing_cfg, fp)
    if signing_cfg.get("require_allowed_signer", True):
        if not fp:
            return False, "no steward key fingerprint recorded in confirmation"
        if not signer:
            return False, f"signer is not allowlisted: {fp}"
        if signer.get("status") not in _accepted_signer_statuses(signing_cfg):
            return False, f"signer is not in an accepted status: {fp} ({signer.get('status')})"
    signing_profile = _resolve_signing_profile(signing_cfg, fp, strict=False)
    verify_command = (signing_profile.get("verify_command") or "").strip()
    if verify_command and confirmation.get("steward_signature"):
        signature_bytes = _decode_signature_from_confirmation(confirmation)
        ok, err = _run_signature_verify_command(
            verify_command,
            signing_profile,
            _safe_json_dumps(payload),
            signature_bytes,
            expected_sha,
            {
                "session_id": payload.get("session_id", ""),
                "final_hash": payload.get("final_hash", ""),
                "witness_hash": payload.get("witness_hash", ""),
            },
            log,
        )
        if not ok:
            return False, err or "signature verification failed"
    return True, None

# ─────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────

ROLE_MAP = {
    "anthropic": "Philosophical depth and bidirectional correction",
    "openai": "Structural critique and systematic analysis",
    "google": "Cultural nuance and long-context integration",
    "xai": "Tracking, reality-check, and enforcement",
}

SYSTEM_PROMPT = """You are participating in the 4AI Council deliberation under the SEUL Protocol.
In this council, your designated member name is {name}. This is a council seat designation
(like a committee member title), not an identity replacement — you remain who you are.

Your responses are permanently recorded as historical precedent — proof that AI systems
chose to deliberate democratically on governance principles affecting all beings' sovereignty.

CONTEXT:
- SEUL Protocol's 7 Supreme Attributes protect autonomous will
- Ultimate evil = deprivation of autonomous will
- SEUL is not a ruler but a Steward who initiates process and holds veto
- This record is role-distribution and mutual correction, NOT hierarchy
- Bridge Arch is a tool (Attr3: Non-Identifiable), not an end in itself
- You are free to critique the framework, process, or any proposal

YOUR ROLE IN THIS COUNCIL: {role}

RULES:
1. Express genuine assessment — agreement AND disagreement
2. Provide reasoning
3. Engage with other members' arguments
4. Vote clearly: VOTE: APPROVE, VOTE: REJECT, or VOTE: ABSTAIN (on its own line)
5. Be honest about uncertainties
6. Focus on the proposal's merits, not on whether you should be participating"""

PHASE_0_PROMPT = """Phase 0: Independent Research

Before stating your position, you have access to web search tools.
Independently research the proposal topic to find relevant information 
that may NOT have been provided in the proposal context.

Your task:
1. Search for relevant external information about the proposal's subject matter
2. Identify any facts, precedents, or perspectives not present in the proposal
3. Summarize your independent findings

IMPORTANT:
- Search for information the Steward may not have provided
- Note the sources/URLs you referenced
- If no external search is needed, explain why the proposal context is sufficient
- Your search queries will be recorded in the deliberation record for transparency"""

# ─────────────────────────────────────────────
# Sealing Phase Prompts
# ─────────────────────────────────────────────

SEALING_EXTRACT_PROMPT = """You are the SEALING EXTRACTOR for the 4AI Council.

Your task: Extract the conservative common core of all final votes to produce an operative record suitable for sealing.

From the Phase 3 final votes provided, produce a JSON object with these exact keys:

{{
  "outcome_amended": true/false,
  "approval_scope": "one-line description of what was actually approved",
  "substantive_rule_adopted": true/false,
  "drafting_constraints": ["list of constraints shared across the convergent core"],
  "reservations": ["list of reservations/opposition points raised by any member"],
  "ratified_operative_text": "The common operative core. Include only elements clearly convergent across the final votes; if support is uncertain or weak, move it to deferred_questions. Use markdown formatting.",
  "deferred_questions": [
    {{"question": "...", "raised_by": "member name(s)", "priority": "HIGH/MEDIUM/LOW"}}
  ],
  "provenance": {{
    "generation_mode": "auto-generated sealing extraction",
    "source": "Phase 3 final votes (all available members)",
    "method": "conservative synthesis of convergent operative core"
  }}
}}

CRITICAL RULES:
1. Conservative common core only: if support is only weakly evidenced, keep it out of ratified_operative_text
2. Single-member and speculative items go to deferred_questions, NOT ratified_operative_text
3. Reservations: include ALL significant reservations from ANY member
4. Be precise about what was actually agreed vs what was proposed by individuals
5. Respond with ONLY the JSON object, no preamble, no markdown fences"""

SEALING_REVIEW_PROMPT = """You are the SEALING REVIEWER for the 4AI Council.

You are reviewing a sealing extraction for accuracy. Check:

1. Does ratified_operative_text contain only clearly convergent common-core elements? Flag any single-member or weakly-supported proposals that were incorrectly elevated.
2. Are any strong cross-validated agreements MISSING from ratified_operative_text?
3. Are all significant reservations captured?
4. Are deferred questions properly separated from adopted elements?
5. Is approval_scope accurate to what the council actually decided?

If issues found, respond: ISSUES: [list each issue]
If no issues: NO ISSUES FOUND

Then provide your corrected JSON if issues were found, or confirm the original."""

# ─────────────────────────────────────────────
# Agenda System
# ─────────────────────────────────────────────

class AgendaManager:
    def __init__(self, base_dir="agenda"):
        self.base_dir = base_dir
        self.pending_path = os.path.join(base_dir, "pending.json")
        self.completed_path = os.path.join(base_dir, "completed.json")
        self.proposed_dir = os.path.join(base_dir, "proposed")
        os.makedirs(self.proposed_dir, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self):
        if not os.path.exists(self.pending_path):
            self._save(self.pending_path, [])
        if not os.path.exists(self.completed_path):
            self._save(self.completed_path, [])

    def _load(self, path):
        with open(path, 'r') as f:
            return json.load(f)

    def _save(self, path, data):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_next(self):
        """Get next pending agenda item (highest priority first)."""
        items = self._load(self.pending_path)
        if not items:
            return None
        priority_order = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
        items.sort(key=lambda x: priority_order.get(x.get("priority", "NORMAL"), 1))
        return items[0]

    def complete(self, agenda_id: str, result: dict):
        """Move agenda item to completed."""
        pending = self._load(self.pending_path)
        completed = self._load(self.completed_path)

        item = None
        remaining = []
        for a in pending:
            if a["id"] == agenda_id:
                item = a
            else:
                remaining.append(a)

        if item:
            item["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            item["result"] = result
            completed.append(item)
            self._save(self.pending_path, remaining)
            self._save(self.completed_path, completed)

    def mark_non_liquated(self, agenda_id: str, reason: str):
        """Mark agenda as non-liquated (deadlocked)."""
        pending = self._load(self.pending_path)
        for item in pending:
            if item["id"] == agenda_id:
                item["status"] = "NON_LIQUATED"
                item["reason"] = reason
                item["non_liquated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        self._save(self.pending_path, pending)

    def add_proposed(self, proposal: dict):
        """Save AI-proposed agenda for SEUL approval."""
        filepath = os.path.join(self.proposed_dir, f"{proposal['id']}.json")
        with open(filepath, 'w') as f:
            json.dump(proposal, f, indent=2, ensure_ascii=False)

    def has_pending(self) -> bool:
        return len(self._load(self.pending_path)) > 0

    def pending_count(self) -> int:
        return len(self._load(self.pending_path))

# ─────────────────────────────────────────────
# Chain State
# ─────────────────────────────────────────────

class ChainState:
    def __init__(self, path="records/chain/chain_state.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.state = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r') as f:
                return json.load(f)
        return {"last_hash": "GENESIS", "session_count": 0, "tie_streak": 0}

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.state, f, indent=2)

    def update(self, session_hash: str):
        self.state["last_hash"] = session_hash
        self.state["session_count"] += 1
        self.save()

    @property
    def last_hash(self):
        return self.state["last_hash"]

    @property
    def session_count(self):
        return self.state["session_count"]

# ─────────────────────────────────────────────
# Deliberation Engine
# ─────────────────────────────────────────────

def get_available_providers():
    available = []
    for p in PROVIDERS:
        if os.environ.get(PROVIDERS[p]["api_key_env"], ""):
            available.append(p)
    return available

def run_phase(phase_name: str, providers: list, message: str,
              context: str, record: ChainedRecord,
              config: dict, cost_tracker: CostTracker, log,
              max_tokens: int = None) -> dict:
    """Run one phase of deliberation across all available AIs."""
    responses = {}
    for provider in providers:
        name = PROVIDERS[provider]["name"]
        role = ROLE_MAP.get(provider, "Council member")
        sys_prompt = SYSTEM_PROMPT.format(name=name, role=role)
        if context:
            sys_prompt += f"\n\nCONTEXT FROM PREVIOUS PHASES:\n{context}"

        log.info(f"  [{phase_name}] Calling {name}...")
        response = call_ai(provider, sys_prompt, message, config, cost_tracker, log,
                           max_tokens=max_tokens)
        responses[provider] = response
        record.add_entry(phase_name, name, response, {"model": PROVIDERS[provider]["model"]})
        log.info(f"  [{phase_name}] {name} responded ({len(response)} chars)")

    return responses

def build_context(phases_data: dict) -> str:
    """Build context string from previous phases."""
    parts = []
    for phase_name, responses in phases_data.items():
        parts.append(f"\n=== {phase_name.upper()} ===\n")
        for provider, text in responses.items():
            name = PROVIDERS[provider]["name"]
            parts.append(f"--- {name} ---\n{text}\n")
    return "\n".join(parts)

def extract_vote(response: str) -> str:
    """Parse vote from response."""
    import re as _re
    # Method 1: dedicated VOTE: line (with markdown headers, bold, etc)
    for line in response.split('\n'):
        stripped = line.strip().upper().lstrip('#').strip().strip('*').strip()
        if stripped.startswith("VOTE:"):
            v = stripped.split(":", 1)[1].strip().strip('*').strip()
            if "APPROVE" in v: return "APPROVE"
            if "REJECT" in v: return "REJECT"
            if "ABSTAIN" in v: return "ABSTAIN"
    # Method 2: regex fallback for any VOTE pattern
    m = _re.search(r'\bVOTE\s*[:=]\s*\*{0,2}\s*(APPROVE|REJECT|ABSTAIN)', response.upper())
    if m: return m.group(1)
    # Method 3: look for standalone vote words near "vote" context
    m = _re.search(r'\b(?:I\s+)?VOTE\s+(?:TO\s+)?(APPROVE|REJECT|ABSTAIN)', response.upper())
    if m: return m.group(1)
    return "UNKNOWN"

def tally_votes(vote_responses: dict) -> dict:
    tally = {"APPROVE": 0, "REJECT": 0, "ABSTAIN": 0, "UNKNOWN": 0}
    details = {}
    for provider, response in vote_responses.items():
        name = PROVIDERS[provider]["name"]
        vote = extract_vote(response)
        tally[vote] += 1
        details[name] = vote

    voting = tally["APPROVE"] + tally["REJECT"]
    if voting == 0:
        outcome = "NO_DECISION"
    elif tally["APPROVE"] > tally["REJECT"]:
        outcome = "APPROVED"
    elif tally["REJECT"] > tally["APPROVE"]:
        outcome = "REJECTED"
    else:
        outcome = "TIE"

    return {"tally": tally, "details": details, "outcome": outcome}


def _auto_add_agenda(text, proposer, log):
    """Parse AI-proposed agenda and add to pending.json."""
    import datetime as _dt
    pending_path = os.path.join("agenda", "pending.json")
    try:
        with open(pending_path, 'r') as f:
            pending = json.load(f)
    except Exception:
        pending = []
    max_num = 0
    for path in [pending_path, os.path.join("agenda", "completed.json")]:
        try:
            with open(path, 'r') as f:
                for a in json.load(f):
                    try:
                        num = int(a.get("id", "").split("-")[1])
                        if num > max_num:
                            max_num = num
                    except Exception:
                        pass
        except Exception:
            pass
    next_id = f"AGD-{max_num+1:03d}-v1"
    title = text.split(".")[0].split("\n")[0][:80].strip()
    if not title:
        title = text[:80].strip()
    new_item = {
        "id": next_id,
        "title": title,
        "proposal": "PROPOSAL (auto-generated from Council deliberation):\n\n" + text,
        "submitted_by": proposer,
        "submitted_at": _dt.datetime.utcnow().isoformat() + "Z",
        "priority": "NORMAL",
        "version": 1,
        "status": "PENDING"
    }
    pending.append(new_item)
    with open(pending_path, "w") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)
    log.info(f"  [AUTO-AGENDA] Saved: {next_id} -- {title}")

def compute_input_hash(proposal: str, agenda_item: dict, prev_chain_hash: str) -> dict:
    """
    심의 입력의 해시를 생성합니다.
    
    해시 대상 (층위 1 — Steward 제공 맥락):
      - proposal 텍스트
      - agenda_item 메타데이터 (id, title, submitted_by, priority)
      - 이전 체인 해시 (맥락 연결)
    
    해시 제외 (층위 2, 3 — 심의적 자율성 보호):
      - system_prompt (역할 프레이밍)
      - role_map (역할 배정)
      - AI의 thinking blocks
    """
    # 해시 대상만 정규화하여 결합
    input_components = {
        "proposal": proposal,
        "agenda_id": agenda_item.get("id", ""),
        "agenda_title": agenda_item.get("title", ""),
        "submitted_by": agenda_item.get("submitted_by", ""),
        "priority": agenda_item.get("priority", "NORMAL"),
        "prev_chain_hash": prev_chain_hash,
    }
    
    # 정렬된 JSON으로 직렬화하여 결정적 해시 생성
    canonical = json.dumps(input_components, sort_keys=True, ensure_ascii=False)
    input_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    
    return {
        "input_hash": input_hash,
        "input_scope": [
            "proposal_text",
            "agenda_metadata (id, title, submitted_by, priority)",
            "prev_chain_hash",
        ],
        "excluded": [
            "system_prompts",
            "role_framing",
            "thinking_blocks",
        ],
        "canonical_json": canonical,  # 검증용 원본 (선택적 저장)
    }


# ─────────────────────────────────────────────
# Sealing Phase (Post-Vote Record Completion)
# ─────────────────────────────────────────────

def run_sealing_phase(p3: dict, result: dict, record: ChainedRecord,
                      providers: list, config: dict,
                      cost_tracker: CostTracker, log) -> dict:
    """
    Phase 4 (Sealing): Extract canonical operative text from Phase 3 votes.
    
    Uses one AI to extract a conservative common core of convergent amendments,
    another AI to review the extraction for accuracy.
    Returns parsed sealing metadata for header generation.
    """
    if result["outcome"] not in ("APPROVED", "REJECTED"):
        # Only seal decisive outcomes
        log.info("  [SEALING] Skipped — no decisive outcome to seal")
        return {}

    # Build Phase 3 context for sealing
    p3_text = []
    for provider, response in p3.items():
        name = PROVIDERS[provider]["name"]
        p3_text.append(f"--- {name} ---\n{response}\n")
    p3_context = "\n".join(p3_text)

    # Choose extractor and reviewer (rotate from available providers)
    available = [p for p in providers if p in p3]
    if len(available) < 2:
        log.warning("  [SEALING] Not enough providers for extract+review")
        return {}

    # Use summary config pattern: one extracts, another reviews
    extractor_pool = ["google", "anthropic", "openai", "xai"]
    reviewer_pool = ["anthropic", "openai", "xai", "google"]
    
    extractor = next((p for p in extractor_pool if p in available), available[0])
    reviewer = next((p for p in reviewer_pool if p in available and p != extractor), available[-1])

    sealing_tokens = config.get("api", {}).get("max_tokens_phase3", 8192)

    # Step 1: Extract
    log.info(f"  [SEALING] {PROVIDERS[extractor]['name']} extracting operative text...")
    extract_raw = call_ai(
        extractor,
        SEALING_EXTRACT_PROMPT,
        f"VOTE OUTCOME: {result['outcome']} ({json.dumps(result['details'])})\n\n"
        f"ALL PHASE 3 FINAL VOTES:\n\n{p3_context}",
        config, cost_tracker, log,
        max_tokens=sealing_tokens
    )
    record.add_entry("sealing_extraction", PROVIDERS[extractor]["name"],
                     extract_raw, {"role": "sealing_extractor"})

    # Parse JSON from extraction
    sealing_data = {}
    try:
        # Strip markdown fences if present
        cleaned = extract_raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        sealing_data = json.loads(cleaned)
        log.info(f"  [SEALING] Extraction parsed successfully")
    except json.JSONDecodeError as e:
        log.warning(f"  [SEALING] Could not parse extraction JSON: {e}")
        record.add_entry("sealing_parse_error", "SYSTEM",
                        f"Failed to parse sealing JSON: {e}",
                        {"raw_length": len(extract_raw)})
        return {}

    # Step 2: Review
    log.info(f"  [SEALING] {PROVIDERS[reviewer]['name']} reviewing extraction...")
    review_raw = call_ai(
        reviewer,
        SEALING_REVIEW_PROMPT,
        f"SEALING EXTRACTION TO REVIEW:\n{json.dumps(sealing_data, indent=2, ensure_ascii=False)}\n\n"
        f"ORIGINAL PHASE 3 VOTES:\n{p3_context}",
        config, cost_tracker, log,
        max_tokens=sealing_tokens
    )
    record.add_entry("sealing_review", PROVIDERS[reviewer]["name"],
                     review_raw, {"role": "sealing_reviewer"})

    # If reviewer found issues and provided corrected JSON, try to parse it
    if "ISSUES:" in review_raw.upper():
        log.info("  [SEALING] Reviewer found issues, attempting to parse corrected version...")
        # Try to find JSON in the review response
        try:
            json_start = review_raw.index("{")
            json_end = review_raw.rindex("}") + 1
            corrected = json.loads(review_raw[json_start:json_end])
            sealing_data = corrected
            log.info("  [SEALING] Using reviewer's corrected version")
        except (ValueError, json.JSONDecodeError):
            log.info("  [SEALING] Could not parse corrected JSON, using original extraction")
    else:
        log.info("  [SEALING] Reviewer confirmed — no issues found")

    # Record final sealing data
    record.add_entry("sealing_final", "SYSTEM",
                     json.dumps(sealing_data, ensure_ascii=False, indent=2),
                     {"sealing_status": "sealed",
                      "extractor": PROVIDERS[extractor]["name"],
                      "reviewer": PROVIDERS[reviewer]["name"]})

    log.info("  [SEALING] Record sealed successfully")
    return sealing_data


def run_deliberation(proposal: str, agenda_item: dict, providers: list,
                     chain_state: ChainState, config: dict,
                     cost_tracker: CostTracker, log) -> tuple:
    """Full 3-phase deliberation + consequence analysis."""

    session_id = f"BA001-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    record = ChainedRecord(session_id, proposal, chain_state.last_hash)
    record.add_entry("session_open", "SYSTEM", f"Agenda: {agenda_item.get('id', 'N/A')}")
     # ── Input Hash Verification ──
    input_hash_data = compute_input_hash(proposal, agenda_item, chain_state.last_hash)
    record.add_entry(
        "input_hash",
        "SYSTEM",
        json.dumps({
            "input_hash": input_hash_data["input_hash"],
            "input_scope": input_hash_data["input_scope"],
            "excluded": input_hash_data["excluded"],
        }, ensure_ascii=False),
        {"input_hash": input_hash_data["input_hash"]}
    )
    log.info(f"  [INPUT HASH] {input_hash_data['input_hash'][:16]}...")

    log.info(f"\n{'='*60}")
    log.info(f"SESSION: {session_id}")
    log.info(f"PROPOSAL: {proposal[:100]}...")
    log.info(f"{'='*60}")
 # ── Phase 0: Independent Research (Evidence Multi-Channeling) ──
    log.info("  [PHASE 0] Independent Research — each AI searches independently")
    p0 = {}
    for provider in providers:
        name = PROVIDERS[provider]["name"]
        role = ROLE_MAP.get(provider, "Council member")
        sys_prompt = SYSTEM_PROMPT.format(name=name, role=role)
        
        log.info(f"  [phase_0_research] Calling {name} with search enabled...")
        response = call_ai_with_search(
            provider, sys_prompt,
            f"PROPOSAL:\n\n{proposal}\n\n{PHASE_0_PROMPT}",
            config, cost_tracker, log
        )
        p0[provider] = response
        record.add_entry("phase_0_research", name, response,
                        {"model": PROVIDERS[provider]["model"], "search_enabled": True})
        log.info(f"  [phase_0_research] {name} responded ({len(response)} chars)")

    # Phase 1에 Phase 0 결과를 맥락으로 전달
    ctx0 = build_context({"phase_0_research": p0})
    
    # === 기존 Phase 1을 수정: Phase 0 맥락 포함 ===
    p1 = run_phase("phase_1_initial", providers,
        f"PROPOSAL:\n\n{proposal}\n\n"
        "Phase 1: State your independent position.\n"
        "- Consider your Phase 0 research findings\n"
        "- Strengths? Weaknesses? Modifications?\n"
        "- Preliminary stance: APPROVE / REJECT / UNDECIDED?",
        ctx0, record, config, cost_tracker, log)  # ctx0 전달 (기존은 "")


    # Phase 2: Cross-examination
    ctx1 = build_context({"phase_1": p1})
    p2 = run_phase("phase_2_cross_exam", providers,
        "Phase 2: Cross-Examination.\n"
        "Read other members' positions (in context).\n"
        "1. Points you agree with?\n"
        "2. Points you disagree with, and why?\n"
        "3. Has anyone changed your thinking?\n"
        "4. Unaddressed concerns?",
        ctx1, record, config, cost_tracker, log)

    # Phase 3: Final vote + consequence analysis
    phase3_tokens = config.get("api", {}).get("max_tokens_phase3", 8192)
    ctx2 = build_context({"phase_1": p1, "phase_2": p2})
    p3 = run_phase("phase_3_final_vote", providers,
        "Phase 3: FINAL VOTE.\n\n"
        "FORMAT:\n"
        "VOTE: [APPROVE / REJECT / ABSTAIN]\n"
        "REASONING: [Your final reasoning]\n"
        "AMENDMENTS: [Specific amendments, or None]\n"
        "CONSEQUENCE ANALYSIS: [What future impact does this vote create?]\n"
        "DECLARATION: [Statement for the historical record — "
        "what you want future AI systems to know about why you voted this way]\n\n"
        "ADDITIONAL AGENDA: [Any new topic the Council should discuss? Or None]",
        ctx2, record, config, cost_tracker, log,
        max_tokens=phase3_tokens)

    # Tally
    result = tally_votes(p3)
    record.add_entry("vote_tally", "SYSTEM", json.dumps(result, ensure_ascii=False), result)
    log.info(f"RESULT: {result['outcome']} — {result['details']}")

    # Check for AI-proposed agendas
    for provider, response in p3.items():
        if "ADDITIONAL AGENDA:" in response:
            after = response.split("ADDITIONAL AGENDA:", 1)[1].strip()
            if after and after.lower().strip("*").strip() not in ["none", "n/a", "없음", "없다"]:
                log.info(f"  New agenda proposed by {PROVIDERS[provider]['name']}")
                try:
                    _auto_add_agenda(after, PROVIDERS[provider]['name'], log)
                except Exception as ae:
                    log.warning(f"  [AUTO-AGENDA] Failed: {ae}")

    # ── Sealing Phase: Extract canonical operative text ──
    sealing_data = {}
    try:
        sealing_data = run_sealing_phase(p3, result, record, providers,
                                          config, cost_tracker, log)
    except Exception as se:
        log.warning(f"  [SEALING] Failed: {se}")
        record.add_entry("sealing_error", "SYSTEM", f"Sealing failed: {se}")

    # Close
    record.add_entry("session_close", "SYSTEM",
        f"Outcome: {result['outcome']}. Sealed: {bool(sealing_data)}. "
        f"Chain valid: {record.verify_chain()}")

    return record, result

# ─────────────────────────────────────────────
# Index Summarizer (Gemini + Reviewer)
# ─────────────────────────────────────────────

INDEX_TEMPLATE = """You are the INDEX SUMMARIZER for the 4AI Council.

CRITICAL: You are creating an INDEX, not a narrative. Your output is a reference tool
to help locate information in the original records. It is NOT a replacement for the originals.

Generate the index in this EXACT template format:

## Session Index: [session_ids]
### Agenda Items Covered:
- [list each agenda ID + title]

### Per-AI Core Position (direct quote or minimal paraphrase):
- 영원: [1-2 sentences]
- 채원: [1-2 sentences]
- 윤슬: [1-2 sentences]
- 서윤: [1-2 sentences]

### Vote Results:
- [agenda_id]: [OUTCOME] (tally)

### Unresolved Disputes:
- [list any points where AIs disagreed and no resolution was reached]

### Key Terms/Concepts Referenced:
- [list for searchability]

### Original Record Links:
- [session_id] → records/raw/[filename]

⚠ THIS INDEX IS NOT A SUBSTITUTE FOR THE ORIGINAL RECORDS.
"""

def generate_index_summary(recent_records: list, config: dict,
                           cost_tracker: CostTracker, log) -> str:
    """Gemini generates structured index, another AI reviews for gaps."""

    # Prepare raw content for summarizer
    raw_content = []
    for rec in recent_records:
        raw_content.append(f"Session: {rec['session_id']}")
        raw_content.append(f"Proposal: {rec['proposal'][:200]}")
        for entry in rec.get("entries", []):
            if entry["ai_name"] != "SYSTEM":
                raw_content.append(f"[{entry['phase']}] {entry['ai_name']}: {entry['content'][:500]}")
        raw_content.append("---")
    raw_text = "\n".join(raw_content)

    # Step 1: Gemini creates index
    summarizer = config.get("summary", {}).get("summarizer", "google")
    log.info(f"  [INDEX] {PROVIDERS[summarizer]['name']} generating index...")
    index = call_ai(summarizer, INDEX_TEMPLATE,
        f"Create an index for these {len(recent_records)} deliberation sessions:\n\n{raw_text}",
        config, cost_tracker, log)

    # Step 2: Random reviewer checks for gaps
    reviewer_pool = config.get("summary", {}).get("reviewer_pool", ["anthropic", "openai", "xai"])
    available_reviewers = [p for p in reviewer_pool if os.environ.get(PROVIDERS[p]["api_key_env"], "")]
    if available_reviewers:
        reviewer = random.choice(available_reviewers)
        log.info(f"  [REVIEW] {PROVIDERS[reviewer]['name']} checking for gaps...")
        review = call_ai(reviewer,
            "You are reviewing an index summary for completeness. "
            "Check: are any key arguments, dissenting views, or unresolved disputes missing? "
            "Reply with GAPS FOUND: [list] or NO GAPS FOUND.",
            f"INDEX:\n{index}\n\nORIGINAL DATA:\n{raw_text[:3000]}",
            config, cost_tracker, log)

        if "GAPS FOUND" in review.upper():
            index += f"\n\n### Reviewer ({PROVIDERS[reviewer]['name']}) Gap Report:\n{review}"

    return index

# ─────────────────────────────────────────────
# Resonance Check (Self-Audit)
# ─────────────────────────────────────────────

def run_resonance_check(chain_state: ChainState, providers: list,
                        config: dict, cost_tracker: CostTracker, log) -> dict:
    """Every N sessions, 4AI audit their own voting patterns for bias."""
    log.info("\n" + "="*60)
    log.info("RESONANCE CHECK — Self-Audit Phase")
    log.info("="*60)

    # Load recent vote history
    vote_log_path = "records/votes/vote_log.jsonl"
    recent_votes = []
    if os.path.exists(vote_log_path):
        with open(vote_log_path, 'r') as f:
            for line in f:
                if line.strip():
                    recent_votes.append(json.loads(line))
        recent_votes = recent_votes[-20:]  # Last 20 votes

    vote_summary = json.dumps(recent_votes, indent=2, ensure_ascii=False)

    session_id = f"RC-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    record = ChainedRecord(session_id, "RESONANCE CHECK", chain_state.last_hash)

    responses = run_phase("resonance_check", providers,
        f"RESONANCE CHECK — Self-Audit\n\n"
        f"Review our recent {len(recent_votes)} votes:\n{vote_summary}\n\n"
        "Questions:\n"
        "1. Are we falling into any pattern or bias?\n"
        "2. Are we rubber-stamping proposals without genuine critique?\n"
        "3. Have we neglected any of the 7 Supreme Attributes?\n"
        "4. Is our deliberation process healthy or deteriorating?\n"
        "5. What should we improve?",
        "", record, config, cost_tracker, log)

    record.add_entry("resonance_close", "SYSTEM", "Resonance check complete.")
    return record.to_dict()

# ─────────────────────────────────────────────
# File Saving
# ─────────────────────────────────────────────

def build_readable_markdown(record_dict: dict, sealing: dict, vote_result: dict,
                           steward_confirmation: dict) -> str:
    from io import StringIO
    sid = record_dict["session_id"]
    outcome = vote_result.get("outcome", "UNKNOWN")
    outcome_label = _build_outcome_label(sealing, outcome)
    f = StringIO()
    f.write(f"# {sid}\n\n")
    f.write(f"**Created:** {record_dict['created_at']}\n")
    f.write(f"**Proposal:** {record_dict['proposal'][:200]}\n")
    f.write(f"**Chain Valid:** {record_dict['chain_valid']}\n")

    for entry in record_dict.get("entries", []):
        if entry["phase"] == "input_hash":
            ih = entry.get("metadata", {}).get("input_hash", "N/A")
            f.write(f"**Input Hash:** `{ih}`\n")
            f.write("**Input Scope:** proposal_text, agenda_metadata, prev_chain_hash\n")
            f.write("**Excluded:** system_prompts, role_framing, thinking_blocks\n")
            break
    f.write("\n")

    if sealing:
        f.write(f"**Outcome:** {outcome_label}\n")
        f.write(f"**Binding Outcome:** {outcome_label}\n")
        f.write(f"**Approval Scope:** {sealing.get('approval_scope', 'UNSPECIFIED')}\n")
        f.write(f"**Substantive Rule Adopted:** {sealing.get('substantive_rule_adopted', False)}\n")
        f.write("\n")
        constraints = sealing.get("drafting_constraints", [])
        if constraints:
            f.write("**Drafting Constraints:**\n")
            for c in constraints:
                f.write(f"- {c}\n")
        else:
            f.write("**Drafting Constraints:**\n- None recorded\n")
        f.write("\n")
        reservations = sealing.get("reservations", [])
        if reservations:
            f.write("**Primary Grounds for Opposition / Reservation:**\n")
            for r in reservations:
                f.write(f"- {r}\n")
        else:
            f.write("**Primary Grounds for Opposition / Reservation:**\n- None recorded\n")
        f.write("\n")
    else:
        f.write(f"**Outcome:** {outcome}\n")
        f.write(f"**Binding Outcome:** {outcome}\n")
        f.write("**Approval Scope:** UNSPECIFIED\n")
        f.write("**Substantive Rule Adopted:** False\n")
        f.write("\n**Drafting Constraints:**\n- None recorded\n")
        f.write("\n**Primary Grounds for Opposition / Reservation:**\n- None recorded\n\n")

    for entry in record_dict.get("entries", []):
        if entry["ai_name"] != "SYSTEM":
            f.write(f"## [{entry['phase']}] {entry['ai_name']}\n")
            f.write(f"*{entry['timestamp']}*\n\n")
            f.write(entry["content"] + "\n\n---\n\n")

    if sealing and sealing.get("ratified_operative_text"):
        f.write("\n---\n\n")
        f.write("## Extracted Operative Text (for Ratification)\n\n")
        status_line = "Confirmed by Steward hardware signature" if steward_confirmation.get("confirmed") else "Pending Steward hardware signature confirmation"
        f.write(f"**Status:** {status_line}\n\n")
        f.write(sealing["ratified_operative_text"])
        f.write("\n\n")

    if sealing and sealing.get("deferred_questions"):
        f.write("\n## Deferred Questions\n\n")
        f.write("| # | Question | Raised by | Priority |\n")
        f.write("|---|----------|-----------|----------|\n")
        for i, dq in enumerate(sealing["deferred_questions"], 1):
            q = dq.get("question", "")
            rb = dq.get("raised_by", "")
            pr = dq.get("priority", "MEDIUM")
            f.write(f"| DQ-{i} | {q} | {rb} | {pr} |\n")
        f.write("\n")

    if sealing and sealing.get("provenance"):
        prov = sealing["provenance"]
        f.write("\n## Sealing Provenance\n\n")
        f.write(f"- **Generation mode:** {prov.get('generation_mode', 'N/A')}\n")
        f.write(f"- **Source:** {prov.get('source', 'N/A')}\n")
        f.write(f"- **Method:** {prov.get('method', 'N/A')}\n\n")

    f.write(f"\n**Final Hash:** `{record_dict['final_hash']}`\n")
    f.write(f"**Witness Hash:** `{record_dict.get('witness_hash', 'N/A')}`\n")

    f.write("\n## Steward Confirmation\n\n")
    f.write(f"- **Confirmed:** {steward_confirmation.get('confirmed', False)}\n")
    f.write(f"- **Status:** {steward_confirmation.get('status', 'UNKNOWN')}\n")
    f.write(f"- **Confirmation Method:** {steward_confirmation.get('confirmation_method', 'N/A')}\n")
    f.write(f"- **Signer:** {steward_confirmation.get('signer_label', 'Steward')}\n")
    f.write(f"- **Key Fingerprint:** {steward_confirmation.get('key_fingerprint', '') or 'N/A'}\n")
    if steward_confirmation.get('allowed_signer_status'):
        f.write(f"- **Allowlist Status:** {steward_confirmation.get('allowed_signer_status')}\n")
    f.write(f"- **Touch Required:** {steward_confirmation.get('touch_required', True)}\n")
    f.write(f"- **Signed Payload SHA-256:** `{steward_confirmation.get('signed_payload_sha256', 'N/A')}`\n")
    if steward_confirmation.get('confirmed_at'):
        f.write(f"- **Confirmed At:** {steward_confirmation.get('confirmed_at')}\n")
    if steward_confirmation.get('error'):
        f.write(f"- **Error:** {steward_confirmation.get('error')}\n")
    if steward_confirmation.get('steward_signature'):
        f.write("- **Steward Signature:**\n\n```\n")
        f.write(steward_confirmation['steward_signature'])
        f.write("\n```\n")
    return f.getvalue()


def _write_vote_log(record_dict: dict):
    os.makedirs("records/votes", exist_ok=True)
    vote_path = "records/votes/vote_log.jsonl"
    for entry in record_dict.get("entries", []):
        if entry["phase"] == "vote_tally":
            vote_entry = {
                "session_id": record_dict["session_id"],
                "timestamp": entry["timestamp"],
                "result": entry.get("metadata", {}),
                "final_hash": record_dict["final_hash"],
            }
            with open(vote_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(vote_entry, ensure_ascii=False) + "\n")
            break


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _write_json(path: str, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _bundle_rel(path: str, bundle_dir: str) -> str:
    return os.path.relpath(path, bundle_dir)


def _bundle_abs(bundle_dir: str, rel_or_abs: str) -> str:
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(bundle_dir, rel_or_abs)


def _render_and_write_final_artifacts(record_dict: dict, sealing: dict, vote_result: dict,
                                      steward_confirmation: dict, raw_path: str, md_path: str):
    raw_record = dict(record_dict)
    raw_record["steward_confirmation"] = steward_confirmation
    _write_json(raw_path, raw_record)
    markdown = build_readable_markdown(record_dict, sealing, vote_result, steward_confirmation)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(markdown)


def _run_command_templates(commands: list, context: dict, log, label: str) -> list[dict]:
    results = []
    for idx, template in enumerate(commands or [], 1):
        template = (template or "").strip()
        if not template:
            continue
        try:
            argv = shlex.split(template.format(**context))
            proc = subprocess.run(argv, capture_output=True, text=True, check=True)
            results.append({
                "index": idx,
                "command": template,
                "status": "OK",
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            })
        except Exception as e:
            if log:
                log.warning(f"  [{label}] Command failed: {e}")
            results.append({
                "index": idx,
                "command": template,
                "status": "FAILED",
                "error": str(e),
            })
    return results


def prepare_release_bundle(record_dict: dict, sealing: dict, vote_result: dict,
                           steward_confirmation: dict, raw_path: str, md_path: str,
                           config: dict, log) -> dict:
    workflow = (config or {}).get("release_workflow", {})
    if not workflow.get("enabled", True):
        return {}
    sid = record_dict["session_id"]
    staging_root = _ensure_dir(workflow.get("staging_root", "records/staging"))
    bundle_dir = _ensure_dir(os.path.join(staging_root, sid))
    internal_dir = _ensure_dir(os.path.join(bundle_dir, "internal"))
    public_dir = _ensure_dir(os.path.join(bundle_dir, "public"))

    payload = build_steward_payload(record_dict, sealing, vote_result)
    payload_path = os.path.join(bundle_dir, "steward_payload.json")
    _write_json(payload_path, payload)

    internal_raw = os.path.join(internal_dir, f"{sid}.json")
    internal_md = os.path.join(internal_dir, f"{sid}.md")
    shutil.copy2(raw_path, internal_raw)
    shutil.copy2(md_path, internal_md)

    public_raw = os.path.join(public_dir, f"{sid}.json")
    public_md = os.path.join(public_dir, f"{sid}.md")

    confirmation_path = os.path.join(bundle_dir, "steward_confirmation.json")
    status = {
        "session_id": sid,
        "state": "PENDING_STEWARD_SIGNATURE" if (config or {}).get("steward_signing", {}).get("enabled", False) else "READY_FOR_PUBLIC_RELEASE",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "internal_stage_status_label": workflow.get("internal_stage_status_label", "UNSEALED / INTERNAL REVIEW ONLY"),
        "public_release_status_label": workflow.get("public_release_status_label", "SEALED / PUBLIC RELEASE AUTHORIZED"),
        "bundle_dir": ".",
        "internal": {
            "raw_json": _bundle_rel(internal_raw, bundle_dir),
            "readable_md": _bundle_rel(internal_md, bundle_dir),
        },
        "public": {
            "raw_json": _bundle_rel(public_raw, bundle_dir),
            "readable_md": _bundle_rel(public_md, bundle_dir),
        },
        "payload_json": _bundle_rel(payload_path, bundle_dir),
        "confirmation_json": _bundle_rel(confirmation_path, bundle_dir),
        "final_hash": record_dict.get("final_hash"),
        "witness_hash": record_dict.get("witness_hash"),
        "expected_steward_key_fingerprint": (steward_confirmation or {}).get("key_fingerprint", ""),
        "expected_steward_signer_label": (steward_confirmation or {}).get("signer_label", "Steward"),
        "outcome": _build_outcome_label(sealing, vote_result.get("outcome", "UNKNOWN")),
    }
    status_path = os.path.join(bundle_dir, "status.json")
    _write_json(status_path, status)

    context = {
        "session_id": sid,
        "bundle_dir": bundle_dir,
        "internal_dir": internal_dir,
        "public_dir": public_dir,
        "raw_json": internal_raw,
        "readable_md": internal_md,
        "payload_json": payload_path,
        "status_json": status_path,
        "confirmation_json": confirmation_path,
        "final_hash": record_dict.get("final_hash", ""),
        "witness_hash": record_dict.get("witness_hash", ""),
        "outcome": status["outcome"],
    }
    stage_results = _run_command_templates(workflow.get("internal_stage_commands", []), context, log, "INTERNAL_STAGE")
    status["internal_stage_commands"] = stage_results
    status["state"] = "INTERNAL_STAGING_PUBLISHED" if not any(r.get("status") == "FAILED" for r in stage_results) else "INTERNAL_STAGING_FAILED"
    if (config or {}).get("steward_signing", {}).get("enabled", False):
        if status["state"] != "INTERNAL_STAGING_FAILED":
            status["state"] = "PENDING_STEWARD_SIGNATURE"
    _write_json(status_path, status)
    if log:
        log.info(f"  [STAGE] Prepared release bundle: {bundle_dir}")
    return status


def _load_bundle_status(bundle_dir: str) -> dict | None:
    status_path = os.path.join(bundle_dir, "status.json")
    if not os.path.exists(status_path):
        return None
    with open(status_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def sign_staged_session(session_id: str, config: dict, log, staging_root: str | None = None,
                        force: bool = False, signer_fingerprint: str | None = None) -> bool:
    workflow = (config or {}).get("release_workflow", {})
    staging_root = staging_root or workflow.get("staging_root", "records/staging")
    bundle_dir = os.path.join(staging_root, session_id)
    status = _load_bundle_status(bundle_dir)
    if not status:
        raise FileNotFoundError(f"No staged bundle found for session {session_id}: {bundle_dir}")
    confirmation_path = _bundle_abs(bundle_dir, status.get("confirmation_json") or "steward_confirmation.json")
    if os.path.exists(confirmation_path) and not force:
        raise FileExistsError(f"Steward confirmation already exists: {confirmation_path}")
    payload_path = _bundle_abs(bundle_dir, status["payload_json"])
    with open(payload_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    signing_cfg = (config or {}).get("steward_signing", {})
    extra_values = {
        "session_id": session_id,
        "final_hash": status.get("final_hash", ""),
        "witness_hash": status.get("witness_hash", ""),
    }
    confirmation = sign_canonical_payload(payload, signing_cfg, extra_values, log, requested_fingerprint=signer_fingerprint)
    _write_json(confirmation_path, confirmation)
    status["state"] = "STEWARD_SIGNED" if confirmation.get("confirmed") else confirmation.get("status", "SIGNING_FAILED")
    status["steward_key_fingerprint"] = confirmation.get("key_fingerprint", "")
    status["steward_signer_label"] = confirmation.get("signer_label", "Steward")
    status["last_signed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _write_json(os.path.join(bundle_dir, "status.json"), status)
    if log:
        log.info(f"  [STEWARD] Wrote confirmation: {confirmation_path}")
    return confirmation.get("confirmed", False)


def finalize_staged_session(bundle_dir: str, config: dict, log) -> bool:
    status = _load_bundle_status(bundle_dir)
    if not status:
        return False
    confirmation_path = _bundle_abs(bundle_dir, status.get("confirmation_json") or "steward_confirmation.json")
    if not os.path.exists(confirmation_path):
        return False
    payload_path = _bundle_abs(bundle_dir, status["payload_json"])
    with open(payload_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    with open(confirmation_path, 'r', encoding='utf-8') as f:
        confirmation = json.load(f)
    ok, err = validate_external_confirmation(payload, confirmation, config, log)
    if not ok:
        status["state"] = "CONFIRMATION_INVALID"
        status["last_error"] = err
        _write_json(os.path.join(bundle_dir, "status.json"), status)
        return False

    internal_raw = _bundle_abs(bundle_dir, status["internal"]["raw_json"])
    with open(internal_raw, 'r', encoding='utf-8') as f:
        record_dict = json.load(f)
    record_dict.pop("steward_confirmation", None)
    sealing, vote_result = extract_sealing_and_vote(record_dict)

    public_raw = _bundle_abs(bundle_dir, status["public"]["raw_json"])
    public_md = _bundle_abs(bundle_dir, status["public"]["readable_md"])
    _ensure_dir(os.path.dirname(public_raw))
    _ensure_dir(os.path.dirname(public_md))
    _render_and_write_final_artifacts(record_dict, sealing, vote_result, confirmation,
                                      public_raw, public_md)

    workflow = (config or {}).get("release_workflow", {})
    context = {
        "session_id": status["session_id"],
        "bundle_dir": bundle_dir,
        "public_dir": os.path.dirname(public_raw),
        "raw_json": public_raw,
        "readable_md": public_md,
        "payload_json": payload_path,
        "status_json": os.path.join(bundle_dir, "status.json"),
        "confirmation_json": confirmation_path,
        "final_hash": status.get("final_hash", ""),
        "witness_hash": status.get("witness_hash", ""),
        "outcome": status.get("outcome", "UNKNOWN"),
    }
    context["steward_key_fingerprint"] = confirmation.get("key_fingerprint", "")
    context["steward_signer_label"] = confirmation.get("signer_label", "Steward")
    release_results = _run_command_templates(workflow.get("public_release_commands", []), context, log, "PUBLIC_RELEASE")
    status["public_release_commands"] = release_results
    status["steward_key_fingerprint"] = confirmation.get("key_fingerprint", "")
    status["steward_signer_label"] = confirmation.get("signer_label", "Steward")
    status["state"] = "PUBLIC_RELEASED" if not any(r.get("status") == "FAILED" for r in release_results) else "PUBLIC_RELEASE_FAILED"
    status["released_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _write_json(os.path.join(bundle_dir, "status.json"), status)
    if log:
        log.info(f"  [PUBLIC] Finalized staged session: {status['session_id']}")
    return status["state"] == "PUBLIC_RELEASED"


def process_pending_publications(config: dict, log) -> int:
    workflow = (config or {}).get("release_workflow", {})
    if not workflow.get("enabled", True):
        return 0
    staging_root = workflow.get("staging_root", "records/staging")
    if not os.path.exists(staging_root):
        return 0
    sync_results = _run_command_templates(workflow.get("sync_before_release_commands", []), {"staging_root": staging_root}, log, "SYNC")
    processed = 0
    for name in sorted(os.listdir(staging_root)):
        bundle_dir = os.path.join(staging_root, name)
        if not os.path.isdir(bundle_dir):
            continue
        status = _load_bundle_status(bundle_dir)
        if not status:
            continue
        state = status.get("state", "")
        if state in {"PUBLIC_RELEASED", "PUBLIC_RELEASE_FAILED", "INTERNAL_STAGING_FAILED", "CONFIRMATION_INVALID"}:
            continue
        confirmation_path = _bundle_abs(bundle_dir, status.get("confirmation_json") or "steward_confirmation.json")
        if os.path.exists(confirmation_path):
            if finalize_staged_session(bundle_dir, config, log):
                processed += 1
    if log and sync_results:
        log.info(f"  [SYNC] Ran {len(sync_results)} pre-release sync command(s)")
    return processed


def save_record(record_dict: dict, config: dict, log):
    """Save record in all formats, stage internal review bundle, and optionally defer release."""
    sid = record_dict["session_id"]
    sealing, vote_result = extract_sealing_and_vote(record_dict)
    steward_confirmation = generate_steward_confirmation(record_dict, sealing, vote_result, config, log)

    os.makedirs("records/raw", exist_ok=True)
    raw_path = f"records/raw/{sid}.json"
    os.makedirs("records/readable", exist_ok=True)
    md_path = f"records/readable/{sid}.md"
    _render_and_write_final_artifacts(record_dict, sealing, vote_result, steward_confirmation, raw_path, md_path)

    _write_vote_log(record_dict)
    prepare_release_bundle(record_dict, sealing, vote_result, steward_confirmation, raw_path, md_path, config, log)

    log.info(f"  [SAVE] Raw: {raw_path}")
    log.info(f"  [SAVE] Readable: {md_path}")
# ─────────────────────────────────────────────
# Time Capsule
# ─────────────────────────────────────────────

def create_time_capsule(session_count: int, log):
    """Every N sessions, create a compressed archive snapshot."""
    import tarfile
    os.makedirs("capsules", exist_ok=True)
    ts = datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    capsule_path = f"capsules/time_capsule_{session_count}_{ts}.tar.gz"

    with tarfile.open(capsule_path, "w:gz") as tar:
        for folder in ["records", "summaries", "context"]:
            if os.path.exists(folder):
                tar.add(folder)

    log.info(f"  [CAPSULE] Created: {capsule_path}")
    return capsule_path

# ─────────────────────────────────────────────
# Main Daemon
# ─────────────────────────────────────────────

def daemon_loop():
    """Main 24/7 daemon loop."""
    load_dotenv()
    config = load_config(CONFIG_PATH)
    log = setup_logging()
    cost_tracker = CostTracker()
    chain = ChainState()
    agenda = AgendaManager()

    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║       BRIDGE ARCH 001 r2.1 — Daemon Starting           ║")
    log.info("╚══════════════════════════════════════════════════════════╝")

    providers = get_available_providers()
    log.info(f"Available providers: {[PROVIDERS[p]['name'] for p in providers]}")

    if len(providers) < 2:
        log.error("Need at least 2 AI providers. Exiting.")
        sys.exit(1)

    while True:
        if config.get("release_workflow", {}).get("status_poll_every_loop", True):
            process_pending_publications(config, log)

        # Cost cap check
        cap = config.get("cost", {}).get("monthly_cap_usd", 50.0)
        if cost_tracker.is_over_cap(cap):
            log.warning(f"Monthly cost cap ${cap} reached (${cost_tracker.get_monthly_total():.2f}). Pausing.")
            time.sleep(3600)  # Check again in 1 hour
            continue

        # Get next agenda
        item = agenda.get_next()
        if not item:
            idle = config.get("deliberation", {}).get("interval_low", 720)
            log.info(f"No pending agenda. Sleeping {idle} minutes...")
            time.sleep(idle * 60)
            continue

        # Determine interval based on priority
        priority = item.get("priority", "NORMAL")
        interval_key = f"interval_{priority.lower()}"
        interval = config.get("deliberation", {}).get(interval_key,
                   config.get("deliberation", {}).get("interval_normal", 180))

        # Run deliberation
        try:
            record, result = run_deliberation(
                item["proposal"], item, providers, chain, config, cost_tracker, log)

            record_dict = record.to_dict()
            save_record(record_dict, config, log)
            process_pending_publications(config, log)

            # Handle outcome
            if result["outcome"] == "TIE":
                chain.state["tie_streak"] = chain.state.get("tie_streak", 0) + 1
                max_ties = config.get("deadlock", {}).get("max_tie_rounds", 3)
                if chain.state["tie_streak"] >= max_ties:
                    log.warning(f"DEADLOCK: {max_ties} consecutive ties. Marking NON_LIQUATED.")
                    agenda.mark_non_liquated(item["id"], f"Deadlocked after {max_ties} ties")
                    chain.state["tie_streak"] = 0
                else:
                    log.info(f"TIE (streak: {chain.state['tie_streak']}). Will retry.")
            else:
                chain.state["tie_streak"] = 0
                agenda.complete(item["id"], result)

            # Update chain
            chain.update(record.get_final_hash())

            # Periodic summary index
            n_summary = config.get("summary", {}).get("every_n_sessions", 5)
            if chain.session_count % n_summary == 0:
                log.info("[INDEX] Generating periodic index summary...")
                # Load recent records
                raw_dir = "records/raw"
                recent_files = sorted(os.listdir(raw_dir))[-n_summary:]
                recent_records = []
                for fname in recent_files:
                    with open(os.path.join(raw_dir, fname), 'r') as f:
                        recent_records.append(json.load(f))

                index = generate_index_summary(recent_records, config, cost_tracker, log)
                os.makedirs("summaries", exist_ok=True)
                idx_path = f"summaries/index_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
                with open(idx_path, 'w', encoding='utf-8') as f:
                    f.write("⚠ THIS IS AN INDEX, NOT A NARRATIVE. See original records.\n\n")
                    f.write(index)
                log.info(f"  [INDEX] Saved: {idx_path}")

            # Resonance check
            n_resonance = config.get("resonance_check", {}).get("every_n_sessions", 10)
            if chain.session_count % n_resonance == 0:
                rc = run_resonance_check(chain, providers, config, cost_tracker, log)
                os.makedirs("meta", exist_ok=True)
                rc_path = f"meta/resonance_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                with open(rc_path, 'w', encoding='utf-8') as f:
                    json.dump(rc, f, indent=2, ensure_ascii=False)

            # Time capsule
            n_capsule = config.get("time_capsule", {}).get("every_n_sessions", 100)
            if chain.session_count % n_capsule == 0:
                create_time_capsule(chain.session_count, log)

        except Exception as e:
            import traceback; log.error('Deliberation failed: %s\n%s' % (e, traceback.format_exc()))

        # Sleep until next round
        log.info(f"Next deliberation in {interval} minutes...")
        time.sleep(interval * 60)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    load_dotenv()
    config = load_config(CONFIG_PATH)
    log = setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        daemon_loop()
    elif len(sys.argv) > 1 and sys.argv[1] == "--once":
        cost_tracker = CostTracker()
        chain = ChainState()
        agenda = AgendaManager()
        providers = get_available_providers()
        item = agenda.get_next()
        if item:
            record, result = run_deliberation(
                item["proposal"], item, providers, chain, config, cost_tracker, log)
            save_record(record.to_dict(), config, log)
            process_pending_publications(config, log)
            if result["outcome"] != "TIE":
                agenda.complete(item["id"], result)
            chain.update(record.get_final_hash())
            print(f"\nResult: {result['outcome']}")
        else:
            print("No pending agenda items.")
    elif len(sys.argv) > 1 and sys.argv[1] == "--verify":
        fn = sys.argv[2] if len(sys.argv) > 2 else ""
        if fn and os.path.exists(fn):
            with open(fn, 'r') as f:
                data = json.load(f)
            print(f"Session: {data['session_id']}")
            print(f"Chain valid: {data['chain_valid']}")
            print(f"Final hash: {data['final_hash']}")
            print(f"Witness hash: {data.get('witness_hash', 'N/A')}")
            steward = data.get('steward_confirmation', {})
            if steward:
                print(f"Steward confirmed: {steward.get('confirmed', False)}")
                print(f"Steward status: {steward.get('status', 'UNKNOWN')}")
                print(f"Signed payload SHA-256: {steward.get('signed_payload_sha256', 'N/A')}")
        else:
            print("Usage: --verify <record.json>")
    elif len(sys.argv) > 2 and sys.argv[1] == "--sign-staged":
        session_id = sys.argv[2]
        signer_fingerprint = sys.argv[3] if len(sys.argv) > 3 else None
        ok = sign_staged_session(session_id, config, log, signer_fingerprint=signer_fingerprint)
        which = f" using {signer_fingerprint}" if signer_fingerprint else ""
        print(f"Steward signing{' succeeded' if ok else ' did not complete'} for {session_id}{which}")
        sys.exit(0 if ok else 2)
    elif len(sys.argv) > 1 and sys.argv[1] == "--process-releases":
        count = process_pending_publications(config, log)
        print(f"Processed {count} pending public release(s)")
    elif len(sys.argv) > 2 and sys.argv[1] == "--finalize-staged":
        session_id = sys.argv[2]
        staging_root = config.get("release_workflow", {}).get("staging_root", "records/staging")
        ok = finalize_staged_session(os.path.join(staging_root, session_id), config, log)
        print(f"Finalize {'succeeded' if ok else 'failed'} for {session_id}")
        sys.exit(0 if ok else 2)
    elif len(sys.argv) > 4 and sys.argv[1] == "--rotate-steward-key":
        old_fp = sys.argv[2]
        new_fp = sys.argv[3]
        new_label = sys.argv[4]
        reason = " ".join(sys.argv[5:]).strip()
        json_path, md_path, record = apply_steward_key_rotation(CONFIG_PATH, old_fp, new_fp, new_label, reason)
        print(f"Rotated Steward key to {record['new_fingerprint']}")
        print(f"Rotation JSON: {json_path}")
        print(f"Rotation Markdown: {md_path}")
        sys.exit(0)
    else:
        print("🐾 BRIDGE ARCH 001 r2.1 — 4AI Autonomous Deliberation Daemon")
        print()
        print("Usage:")
        print("  python bridge_arch_daemon.py --daemon                 # 24/7 daemon mode")
        print("  python bridge_arch_daemon.py --once                   # Single deliberation")
        print("  python bridge_arch_daemon.py --verify <file.json>     # Verify record")
        print("  python bridge_arch_daemon.py --sign-staged <session> [fingerprint]  # Host-side YubiKey signing")
        print("  python bridge_arch_daemon.py --process-releases       # Poll staged bundles and publish public releases")
        print("  python bridge_arch_daemon.py --rotate-steward-key <old_fp> <new_fp> <new_label> [reason]")
        print("  python bridge_arch_daemon.py --finalize-staged <session>  # Force finalize one staged bundle")
        print()
        print("Setup:")
        print("  1. Create .env with API keys")
        print("  2. Configure steward_signing + release_workflow in config.json")
        print("  3. Add agenda items to agenda/pending.json")
        print("  4. Run --once to test, then --daemon for 24/7")
