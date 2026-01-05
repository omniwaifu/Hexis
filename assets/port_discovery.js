(() => {
  const portFile = "/hexis_ports.json";

  const parsePort = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num)) {
      return "";
    }
    return String(num);
  };

  const redirectIfNeeded = (frontendPort) => {
    if (!frontendPort) {
      return;
    }
    if (window.location.port === frontendPort) {
      return;
    }
    const url = new URL(window.location.href);
    url.port = frontendPort;
    window.location.replace(url.toString());
  };

  fetch(portFile, { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (!data) {
        return;
      }
      const frontendPort = parsePort(data.frontend_port);
      const backendPort = parsePort(data.backend_port);
      if (backendPort) {
        window.__HEXIS_BACKEND_PORT__ = backendPort;
      }
      redirectIfNeeded(frontendPort);
    })
    .catch(() => {});
})();
