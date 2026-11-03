"""The apimatic-core and apimatic-requests-client-adapter wheels (pulled in by paypal-server-sdk) install a top-level
`tests` package into site-packages. This file makes ours a regular package so the repo root wins on sys.path."""
