# Central DSP Go bidder — статический бинарь, итоговый образ ~15 MB.
# Audit O-07 / C6: оба базовых образа закреплены tag + digest (обновлять
# осознанно, отдельным PR — docs/DEPLOYMENT.md §13). Runtime — distroless
# :nonroot (uid 65532), без shell и пакетного менеджера.
FROM golang:1.26.10-alpine@sha256:27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /bidder ./cmd/bidder

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /bidder /bidder
EXPOSE 8090
ENTRYPOINT ["/bidder"]
