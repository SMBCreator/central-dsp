package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §4.2.1 — BidResponse top-level object.
 *
 * <p>Serialized as JSON and returned to the SSP.
 * {@code @JsonInclude(NON_NULL)} ensures we don't send null fields,
 * which some SSPs reject.</p>
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public class BidResponse {

    /** ID of the bid request to which this is a response. Required. */
    @JsonProperty("id")
    private String id;

    /**
     * Array of seatbid objects; 1+ objects required if a bid is to be made.
     * Absent or empty = no bid.
     */
    @JsonProperty("seatbid")
    private List<SeatBid> seatbid;

    /** Bidder generated response ID to assist with logging/tracking. */
    @JsonProperty("bidid")
    private String bidid;

    /** Bid currency using ISO-4217 alpha codes. Default: "USD". */
    @JsonProperty("cur")
    private String cur;

    /**
     * Optional reason for not bidding (OpenRTB List 5.24).
     * 0=Unknown Error, 1=Technical Error, 2=Invalid Request,
     * 3=Known Web Spider, 4=Suspected Non-Human Traffic,
     * 5=Cloud/Data-Center/Proxy IP, 6=Unsupported Device,
     * 7=Blocked Publisher or Site, 8=Unmatched User.
     */
    @JsonProperty("nbr")
    private Integer nbr;

    // ──────────────────────────────────────────────────────────────────
    // Inner: SeatBid
    // ──────────────────────────────────────────────────────────────────

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class SeatBid {

        /** Array of 1+ Bid objects each related to an impression. Required. */
        @JsonProperty("bid")
        private List<Bid> bid;

        /**
         * ID of the buyer seat (e.g. advertiser, agency) on whose behalf
         * this bid is made.
         */
        @JsonProperty("seat")
        private String seat;

        /**
         * 0 = the exchange can choose to group related bids (default),
         * 1 = impressions must be won or lost as a group.
         */
        @JsonProperty("group")
        private Integer group;
    }

    // ──────────────────────────────────────────────────────────────────
    // Inner: Bid
    // ──────────────────────────────────────────────────────────────────

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class Bid {

        /** Bidder generated bid ID to assist with logging/tracking. Required. */
        @JsonProperty("id")
        private String id;

        /** ID of the Impression object in the related bid request. Required. */
        @JsonProperty("impid")
        private String impid;

        /**
         * Bid price expressed as CPM in ISO-4217 alpha codes.
         * This is the ACTUAL bid; the exchange may apply fees.
         * Required.
         */
        @JsonProperty("price")
        private Double price;

        /**
         * Win notice URL called by the exchange if the bid wins
         * (not necessarily indicative of a delivered, viewed, or billable ad).
         * Substitution macros (e.g. ${AUCTION_PRICE}) will be expanded by the SSP.
         */
        @JsonProperty("nurl")
        private String nurl;

        /**
         * Billing notice URL called by the exchange when a winning bid
         * becomes billable based on exchange-specific business policy
         * (e.g. when the ad is rendered).
         */
        @JsonProperty("burl")
        private String burl;

        /**
         * Loss notice URL called by the exchange when a bid is known to
         * have been lost. Substitution macros will be expanded by the SSP.
         */
        @JsonProperty("lurl")
        private String lurl;

        /**
         * Optional means of conveying ad markup in case the bid wins.
         * Superseded by nurl if both are present in certain protocols.
         */
        @JsonProperty("adm")
        private String adm;

        /** ID of a preloaded ad to be served if the bid wins. */
        @JsonProperty("adid")
        private String adid;

        /** Advertiser domain(s) for block list checking. */
        @JsonProperty("adomain")
        private List<String> adomain;

        /** Bundle or package name of the app being advertised. */
        @JsonProperty("bundle")
        private String bundle;

        /** URL without cache-busting to an image that is representative of the content. */
        @JsonProperty("iurl")
        private String iurl;

        /** Campaign ID to assist with ad quality checking. */
        @JsonProperty("cid")
        private String cid;

        /** Creative ID to assist with ad quality checking. */
        @JsonProperty("crid")
        private String crid;

        /**
         * IAB content categories of the creative.
         */
        @JsonProperty("cat")
        private List<String> cat;

        /** Width of the creative in device independent pixels. */
        @JsonProperty("w")
        private Integer w;

        /** Height of the creative in device independent pixels. */
        @JsonProperty("h")
        private Integer h;

        /**
         * Advisory as to the number of seconds the bidder is willing to
         * wait between the auction and the actual impression.
         */
        @JsonProperty("exp")
        private Integer exp;

        /**
         * Tactic ID to enable buyers to label bids for reporting
         * to the exchange the tactic through which their bid is submitted.
         */
        @JsonProperty("tactic")
        private String tactic;
    }
}
