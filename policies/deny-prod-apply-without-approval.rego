package infraagent

default allow = false

allow {
  input.environment != "prod"
}

allow {
  input.environment == "prod"
  input.approved == true
}

