# Central DSP Go bidder — статический бинарь, итоговый образ ~15 MB.
FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /bidder ./cmd/bidder

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /bidder /bidder
EXPOSE 8090
ENTRYPOINT ["/bidder"]
