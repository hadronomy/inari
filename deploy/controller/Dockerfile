FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2

WORKDIR /app
COPY --chown=65532:65532 inari-server /app/inari-server
COPY --chown=65532:65532 site /app/site

ENV LEPTOS_OUTPUT_NAME=inari-web \
    LEPTOS_SITE_ROOT=/app/site \
    LEPTOS_SITE_PKG_DIR=pkg

USER 65532:65532
EXPOSE 3000
ENTRYPOINT ["/app/inari-server"]
