# Revoked Keys

## 2026-03-12 — Key Compromise Incident

### Incident Summary

- **Date Discovered:** 2026-03-12
- **Attack Vector:** DigitalOcean VM SSH inbound firewall open to public. Unauthorized IPv6 access detected in logs.
- **Exposure:** Private keys (.pem), certificates, public keys, .env (API keys), and daemon configuration files stored on VM were accessible to attacker.
- **VM Status:** Snapshot preserved for forensic reference. Droplet destroyed.
- **Resolution:** All keys listed below are permanently revoked. New keys will be generated on-chip with YubiKey 5C (×4, 3/4 multi-sig). Private keys will never leave hardware.

---

### Revoked Key: Primary Steward Key

| Field | Value |
|-------|-------|
| Label | Primary Steward Key |
| Status | **COMPROMISED — DO NOT TRUST** |
| Key Fingerprint | `52e6057a091c8fb70d0679424da911344607ae9f9908110a260e4bb83c5b5079` |
| Certificate Fingerprint | `c62bee21033591788bab4657616be6900310c8951d02e309e507edbab40101ed` |
| Public Key (b64) | `MCowBQYDK2VwAyEAgO3gO8gFj5ZL6pedwF2nMoS3odykHGKzYPPKZoyMRvY=` |
| Algorithm | ed25519-piv |
| Slot | 9c |
| Revoked At | 2026-03-12 |

### Revoked Key: Backup Steward Key

| Field | Value |
|-------|-------|
| Label | Backup Steward Key |
| Status | **COMPROMISED — DO NOT TRUST** |
| Key Fingerprint | `14e22caf6406f9f2356a8ea826c8fdf10da6d6cd81510e769ffcf526f8fa5bbf` |
| Certificate Fingerprint | `b7ff013648cfe6df0c8d66e41397df7d7f6ad596e18efcb9c434add65687220e` |
| Public Key (b64) | `MCowBQYDK2VwAyEAsZhS9UkXSieidpCuU3gqlQt8xnTSP34NaUaO2VZ+Wdk=` |
| Algorithm | ed25519-piv |
| Slot | 9c |
| Revoked At | 2026-03-12 |

---

### Verification Policy

- **All signatures made with the above keys are considered untrusted.**
- **These keys must NEVER be used for verification of any document, AGD, or protocol action.**
- **Any document bearing signatures from these fingerprints requires re-signing with the new key set.**
- **New key fingerprints will be recorded in this file upon generation.**

---

### New Key Registration

_Pending: YubiKey 5C ×4 delivery. Keys will be generated on-chip (private key never exported). 3/4 multi-sig structure. This section will be updated upon completion._

| Key | Fingerprint | Status |
|-----|-------------|--------|
| Key A | _(pending)_ | _(pending)_ |
| Key B | _(pending)_ | _(pending)_ |
| Key C | _(pending)_ | _(pending)_ |
| Key D (cold backup) | _(pending)_ | _(pending)_ |

---

*Recorded by SEUL (Steward) — 2026-03-12*
