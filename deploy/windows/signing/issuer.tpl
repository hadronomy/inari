{
  "subject": {{ toJson .Subject }},
  "keyUsage": ["certSign", "crlSign"],
  "extKeyUsage": ["codeSigning"],
  "basicConstraints": {
    "isCA": true,
    "maxPathLen": 0
  }
}
