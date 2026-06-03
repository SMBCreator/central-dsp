package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import io.centraldsp.bidder.model.campaign.CreativeDTO;
import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.Impression;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

/**
 * Generates ad markup (adm field in Bid) for different creative formats.
 *
 * Banner → HTML img/iframe snippet
 * Native → JSON following IAB Native Ad Specification 1.1
 * Video  → VAST 2.0 XML wrapper
 */
@Slf4j
@Service
public class AdMarkupService {

    public String buildMarkup(CampaignDTO campaign, CreativeDTO creative,
                              BidRequest request, Impression imp) {
        return switch (creative.getFormat()) {
            case "banner" -> buildBannerMarkup(campaign, creative);
            case "native" -> buildNativeMarkup(campaign, creative);
            case "video"  -> buildVideoMarkup(campaign, creative);
            default -> buildBannerMarkup(campaign, creative);
        };
    }

    // ── Banner ───────────────────────────────────────────────────────────────
    private String buildBannerMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String clickUrl   = escapeHtml(creative.getClickUrl() != null
                ? creative.getClickUrl() : campaign.getClickUrl());
        String imageUrl   = escapeHtml(creative.getImageUrl());
        String pixel      = campaign.getTrackingPixel() != null
                ? "<img src='" + escapeHtml(campaign.getTrackingPixel()) +
                  "' width='1' height='1' style='display:none'/>" : "";
        int w = creative.getWidth()  != null ? creative.getWidth()  : 300;
        int h = creative.getHeight() != null ? creative.getHeight() : 250;

        return "<a href='" + clickUrl + "' target='_blank' rel='noopener'>" +
               "<img src='" + imageUrl + "' width='" + w + "' height='" + h +
               "' border='0' alt=''/>" + pixel + "</a>";
    }

    // ── Native ───────────────────────────────────────────────────────────────
    // Returns IAB Native 1.1 JSON response embedded in adm field
    private String buildNativeMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String title = escapeJson(creative.getTitle() != null ? creative.getTitle() : "");
        String desc  = escapeJson(creative.getDescription() != null ? creative.getDescription() : "");
        String img   = escapeJson(creative.getImageUrl() != null ? creative.getImageUrl() : "");
        String url   = escapeJson(creative.getClickUrl() != null
                ? creative.getClickUrl() : campaign.getClickUrl());
        String pixel = campaign.getTrackingPixel() != null
                ? escapeJson(campaign.getTrackingPixel()) : "";

        return "{\"native\":{\"ver\":\"1.1\"," +
               "\"assets\":[" +
               "{\"id\":1,\"required\":1,\"title\":{\"text\":\"" + title + "\"}}," +
               "{\"id\":2,\"required\":1,\"data\":{\"type\":2,\"value\":\"" + desc + "\"}}," +
               "{\"id\":3,\"required\":1,\"img\":{\"type\":3,\"url\":\"" + img + "\"}}" +
               "]," +
               "\"link\":{\"url\":\"" + url + "\"}," +
               "\"imptrackers\":[\"" + pixel + "\"]" +
               "}}";
    }

    // ── Video (VAST 2.0 Wrapper) ─────────────────────────────────────────────
    private String buildVideoMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String vastUrl = creative.getVastUrl() != null ? creative.getVastUrl() : "";
        String clickUrl = creative.getClickUrl() != null
                ? creative.getClickUrl() : campaign.getClickUrl();
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" +
               "<VAST version=\"2.0\">" +
               "<Ad id=\"" + creative.getId() + "\">" +
               "<Wrapper>" +
               "<AdSystem>CentralDSP</AdSystem>" +
               "<VASTAdTagURI><![CDATA[" + vastUrl + "]]></VASTAdTagURI>" +
               "<Impression><![CDATA[" + (campaign.getTrackingPixel() != null
                       ? campaign.getTrackingPixel() : "") + "]]></Impression>" +
               "<Creatives><Creative>" +
               "<Linear><VideoClicks>" +
               "<ClickThrough><![CDATA[" + clickUrl + "]]></ClickThrough>" +
               "</VideoClicks></Linear>" +
               "</Creative></Creatives>" +
               "</Wrapper></Ad></VAST>";
    }

    private String escapeHtml(String s) {
        if (s == null) return "";
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&#x27;");
    }

    private String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }
}
