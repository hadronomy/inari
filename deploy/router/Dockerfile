FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2

WORKDIR /app
COPY --chmod=0755 inari-router /app/inari-router
COPY --chmod=0755 zenohd /usr/local/bin/zenohd

USER 65532:65532
EXPOSE 7447 8443
ENTRYPOINT ["/app/inari-router"]
