# Security policy

Report vulnerabilities privately through the repository's GitHub security advisory form. Do not include credentials, production serial captures, or device identifiers in a public issue.

The reference service is designed for a trusted local network. It does not implement authentication or TLS termination. Put it behind an authenticated reverse proxy before exposing it beyond that boundary, restrict access to the serial device, and run it as the unprivileged service user supplied in `deploy/sensor-monitor.service`.
