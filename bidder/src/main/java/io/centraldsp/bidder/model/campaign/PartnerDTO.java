package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

/** Supply partner (SSP) row from the backend feed. */
@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class PartnerDTO {
    private Long id;

    @JsonProperty("endpoint_key")
    private String endpointKey;

    /** Shared secret the SSP sends in x-centraldsp-token. Null/empty = no auth. */
    @JsonProperty("auth_token")
    private String authToken;

    @JsonProperty("qps_limit")
    private Integer qpsLimit;

    @JsonProperty("bid_floor")
    private Double bidFloor;

    private String status;
}
