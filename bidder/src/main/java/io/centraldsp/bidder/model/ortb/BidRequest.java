package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import java.util.List;

/**
 * OpenRTB 2.5 BidRequest — top-level object received from SSP/Exchange.
 * Only fields relevant for targeting and bidding are mapped.
 */
@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class BidRequest {

    /** Unique ID from SSP for this auction. */
    @JsonProperty("id")
    private String id;

    /** List of impression opportunities in this request. */
    @JsonProperty("imp")
    private List<Impression> imp;

    /** Site context (web traffic). */
    @JsonProperty("site")
    private Site site;

    /** App context (in-app traffic). */
    @JsonProperty("app")
    private App app;

    /** Device info. */
    @JsonProperty("device")
    private Device device;

    /** User info (optional). */
    @JsonProperty("user")
    private User user;

    /** Maximum time (ms) DSP has to respond. */
    @JsonProperty("tmax")
    private Integer tmax;

    /** Auction type: 1=first-price, 2=second-price. */
    @JsonProperty("at")
    private Integer at;

    /** Test flag: 1=test (no real money). */
    @JsonProperty("test")
    private Integer test;
}
