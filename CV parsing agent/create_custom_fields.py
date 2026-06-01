#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create custom fields on the SS environment — run this ONCE (or when you need
new fields).  Does not run the full parsing pipeline.

Writes pipeline_output/custom_fields_manifest.json so custom_field_pipeline.py
can load field_id + column mapping without POSTing again.

Usage:
  python3 create_custom_fields.py

Re-running will attempt to create duplicate fields if they already exist on
the account — only run when fields are missing or use a fresh test account.
"""

from custom_field_pipeline import (
    BASE_URL_SS,
    CUSTOM_FIELDS,
    CUSTOM_FIELDS_MANIFEST,
    EMAIL_SS,
    PASSWORD_SS,
    create_custom_fields,
    get_token,
    save_custom_fields_manifest,
)


def main():
    print("=" * 60)
    print("Create custom fields (SS env only)")
    print("=" * 60)
    print(f"Target: {BASE_URL_SS}")
    print(f"Fields: {len(CUSTOM_FIELDS)} (from CUSTOM_FIELDS in custom_field_pipeline.py)")
    print()

    token = get_token(BASE_URL_SS, EMAIL_SS, PASSWORD_SS)
    print("Token OK\n")

    created = create_custom_fields(token)
    if not created:
        print("\nNo fields were created. Fix errors above and retry.")
        return

    save_custom_fields_manifest(created)
    print(f"\nManifest written → {CUSTOM_FIELDS_MANIFEST}")
    print(f"Successfully created {len(created)} / {len(CUSTOM_FIELDS)} field(s).")
    print("\nYou can now run:  python3 custom_field_pipeline.py")


if __name__ == "__main__":
    main()
