package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.util.List;

/**
 * OpenRTB 2.5 BidResponse — our answer to SSP.
 * Null fields are excluded from JSON serialization.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonInclude(JsonInclude.Include.NON_NULL)
public class BidResponse {

    @JsonProperty("id")
    private String id;

    @JsonProperty("seatbid")
    private List<SeatBid> seatbid;

    @JsonProperty("bidid")
    private String bidid;

    @JsonProperty("cur")
    @Builder.Default
    private String cur = "USD";

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class SeatBid {
        @JsonProperty("bid")
        private List<Bid> bid;

        @JsonProperty("seat")
        private String seat;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class Bid {
        /** Bidder-generated unique bid ID. */
        @JsonProperty("id")
        private String id;

        /** Must match imp.id from BidRequest. */
        @JsonProperty("impid")
        private String impid;

        /** Bid price in CPM (USD). Must be > bid floor. */
        @JsonProperty("price")
        private Double price;

        /** Win notice URL — SSP fires on win. Includes ${AUCTION_PRICE} macro. */
        @JsonProperty("nurl")
        private String nurl;

        /** Loss notice URL — SSP fires on loss. */
        @JsonProperty("lurl")
        private String lurl;

        /** Ad markup (HTML for banner, VAST XML for video, JSON for native). */
        @JsonProperty("adm")
        private String adm;

        /** Advertiser domain(s) for block-list checking. */
        @JsonProperty("adomain")
        private List<String> adomain;

        /** Creative ID — used for frequency cap and deduplication. */
        @JsonProperty("crid")
        private String crid;

        /** Campaign ID (our internal ID). */
        @JsonProperty("cid")
        private String cid;

        /** Banner width. */
        @JsonProperty("w")
        private Integer w;

        /** Banner height. */
        @JsonProperty("h")
        private Integer h;
    }
}
