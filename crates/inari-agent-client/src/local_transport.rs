use std::net::{IpAddr, Ipv4Addr, SocketAddr};

use reqwest::dns::{Addrs, Name, Resolve, Resolving};
use url::{Host, Url};

use crate::{AgentClientError, AgentClientResult};

/// Local authentication needs a loopback peer. The URL hostname still identifies the TLS certificate.
pub(crate) struct LoopbackResolver;

impl Resolve for LoopbackResolver {
    fn resolve(&self, _: Name) -> Resolving {
        Box::pin(async {
            let addresses: Addrs =
                Box::new(std::iter::once(SocketAddr::from((Ipv4Addr::LOCALHOST, 0))));
            Ok(addresses)
        })
    }
}

pub(crate) fn socket_address(endpoint: &Url) -> AgentClientResult<SocketAddr> {
    let host = match endpoint.host() {
        Some(Host::Domain(_)) => IpAddr::V4(Ipv4Addr::LOCALHOST),
        Some(Host::Ipv4(ip)) if ip.is_loopback() => IpAddr::V4(ip),
        Some(Host::Ipv6(ip)) if ip.is_loopback() => IpAddr::V6(ip),
        _ => {
            return Err(AgentClientError::invalid_response(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "The local Agent Endpoint needs a certificate hostname or a loopback address.",
            )));
        },
    };
    let port = endpoint
        .port_or_known_default()
        .ok_or_else(|| {
            AgentClientError::invalid_response(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "The local Agent Endpoint has no listener port.",
            ))
        })?;
    Ok(SocketAddr::new(host, port))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_remote_ip_endpoints() {
        for endpoint in ["https://192.0.2.1:7310/", "https://[2001:db8::1]:7310/"] {
            assert!(socket_address(&Url::parse(endpoint).unwrap()).is_err());
        }
    }

    #[test]
    fn preserves_ipv6_loopback_and_the_listener_port() {
        assert_eq!(
            socket_address(&Url::parse("https://[::1]:7310/").unwrap()).unwrap(),
            "[::1]:7310".parse().unwrap()
        );
    }
}
