#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick verification script to test TS2 environment connectivity
Run this before the full load test to verify configuration
"""

import requests
import sys

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Test configurations
ENVIRONMENTS = {
    "ts2": {
        "url": "https://albatross-ts2.recruitcrm.net",
        "email": os.getenv("TS2_EMAIL", ""),
        "password": os.getenv("TS2_PASSWORD", "")
    },
    "staging": {
        "url": "https://albatross-staging.recruitcrm.net",
        "email": os.getenv("TS2_EMAIL", ""),
        "password": os.getenv("TS2_PASSWORD", "")
    }
}

def test_login(env_name: str, config: dict):
    """Test login to an environment"""
    print(f"\n{'='*60}")
    print(f"Testing {env_name.upper()} environment")
    print(f"{'='*60}")
    print(f"URL: {config['url']}")
    print(f"Email: {config['email']}")
    
    try:
        print("Attempting login...", end=" ")
        response = requests.post(
            f"{config['url']}/v1/login",
            data={"email": config["email"], "password": config["password"]},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "token" in data["data"]:
                print("✅ SUCCESS")
                print(f"Token received: {data['data']['token'][:20]}...")
                return True
            else:
                print("❌ FAILED - Token not found in response")
                print(f"Response: {data}")
                return False
        else:
            print(f"❌ FAILED - Status code: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
            
    except requests.exceptions.ConnectionError as e:
        print(f"❌ CONNECTION ERROR")
        print(f"Error: {e}")
        print(f"The URL '{config['url']}' may not exist or is unreachable")
        return False
    except Exception as e:
        print(f"❌ ERROR")
        print(f"Error: {e}")
        return False

def main():
    print("\n" + "="*60)
    print("TS2 ENVIRONMENT VERIFICATION")
    print("="*60)
    
    results = {}
    
    # Test both environments
    for env_name, config in ENVIRONMENTS.items():
        results[env_name] = test_login(env_name, config)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for env_name, success in results.items():
        status = "✅ READY" if success else "❌ FAILED"
        print(f"{env_name.upper()}: {status}")
    
    # Recommendations
    print("\n" + "="*60)
    print("RECOMMENDATIONS")
    print("="*60)
    
    if results.get("ts2"):
        print("✅ TS2 environment is accessible and ready for load testing")
        print("   Run: python3 load_tester.py")
    elif results.get("staging"):
        print("⚠️  TS2 environment failed, but STAGING is accessible")
        print("   Consider using staging URL in load_tester.py:")
        print("   TS2_BASE = 'https://albatross-staging.recruitcrm.net'")
    else:
        print("❌ Both environments failed")
        print("   Possible issues:")
        print("   1. Wrong URLs - check with your team")
        print("   2. Network issues - check VPN/connectivity")
        print("   3. Wrong credentials - verify account details")
        print("   4. Environments are down - check status")
    
    print("\n")
    
    # Exit code
    sys.exit(0 if results.get("ts2") else 1)

if __name__ == "__main__":
    main()
