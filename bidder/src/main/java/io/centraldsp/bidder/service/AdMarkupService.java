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
 * <ul>
 *   <li>Banner  → HTML img+click+pixel snippet</li>
 *   <li>Native  → IAB Native Ads Specification 1.1 JSON</li>
 *   <li>Video   → VAST 2.0 Wrapper (vastUrl) or Inline (videoUrl only)</li>
 * </ul>
 *
 * <p>If creative.adm is already set it is returned as-is (raw pass-through).</p>
 */
@Slf4j
@Service
public class AdMarkupService {

    public String buildMarkup(CampaignDTO campaign, CreativeDTO creative,
                              BidRequest request, Impression imp) {
        // Raw markup pass-through: creative overrides everything.
        if (creative.getAdm() != null && !creative.getAdm().isBlank()) {
            return creative.getAdm();
        }
        return switch (creative.getFormat()) {
            case "banner" -> buildBannerMarkup(campaign, creative);
            case "native" -> buildNativeMarkup(campaign, creative);
            case "video"  -> buildVideoMarkup(campaign, creative);
            default       -> buildBannerMarkup(campaign, creative);
        };
    }

    // ── Banner ────────────────────────────────────────────────────────────────────────
    private String buildBannerMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String clickUrl = escapeHtml(creative.getClickUrl() != null
                ? creative.getClickUrl() : campaign.getClickUrl());
        String imageUrl = escapeHtml(creative.getImageUrl());
        String pixel    = campaign.getTrackingPixel() != null
                ? "<img src='" + escapeHtml(campaign.getTrackingPixel()) +
                  "' width='1' height='1' style='display:none'/>" : "";
        int w = creative.getWidth()  != null ? creative.getWidth()  : 300;
        int h = creative.getHeight() != null ? creative.getHeight() : 250;

        return "<a href='" + clickUrl + "' target='_blank' rel='noopener'>" +
               "<img src='" + imageUrl + "' width='" + w + "' height='" + h +
               "' border='0' alt=''/>" + pixel + "</a>";
    }

    // ── Native ─────────────────────────────────────────────────────────────────────
    // IAB Native Ads Specification 1.1 — adm is a JSON string
    private String buildNativeMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String title = escapeJson(creative.getTitle()       != null ? creative.getTitle()       : "");
        String desc  = escapeJson(creative.getDescription() != null ? creative.getDescription() : "");
        String img   = escapeJson(creative.getImageUrl()    != null ? creative.getImageUrl()    : "");
        String icon  = escapeJson(creative.getIconUrl()     != null ? creative.getIconUrl()     : "");
        String spons = escapeJson(creative.getSponsoredBy() != null ? creative.getSponsoredBy() : "");
        String url   = escapeJson(creative.getClickUrl()    != null
                ? creative.getClickUrl() : (campaign.getClickUrl() != null ? campaign.getClickUrl() : ""));
        String pixel = campaign.getTrackingPixel() != null
                ? escapeJson(campaign.getTrackingPixel()) : "";

        // Build assets array; icon and sponsor are optional
        StringBuilder assets = new StringBuilder();
        assets.append("{\"id\":1,\"required\":1,\"title\":{\"text\":\"").append(title).append("\"}},");
        assets.append("{\"id\":2,\"required\":1,\"data\":{\"type\":2,\"value\":\"").append(desc).append("\"}},");
        assets.append("{\"id\":3,\"required\":1,\"img\":{\"type\":3,\"url\":\"").append(img).append("\"}}");
        if (!icon.isEmpty()) {
            assets.append(",{\"id\":4,\"img\":{\"type\":1,\"url\":\"").append(icon).append("\"}}");
        }
        if (!spons.isEmpty()) {
            assets.append(",{\"id\":5,\"data\":{\"type\":1,\"value\":\"").append(spons).append("\"}}");
        }

        return "{\"native\":{\"ver\":\"1.1\"," +
               "\"assets\":[" + assets + "]," +
               "\"link\":{\"url\":\"" + url + "\"}," +
               (pixel.isEmpty() ? "\"imptrackers\":[]" :
                       "\"imptrackers\":[\"" + pixel + "\"]") +
               "}}";
    }

    // ── Video ────────────────────────────────────────────────────────────────────
    private String buildVideoMarkup(CampaignDTO campaign, CreativeDTO creative) {
        String clickUrl  = creative.getClickUrl() != null
                ? creative.getClickUrl() : (campaign.getClickUrl() != null ? campaign.getClickUrl() : "");
        String impression = campaign.getTrackingPixel() != null ? campaign.getTrackingPixel() : "";
        String adId      = String.valueOf(creative.getId());

        // VAST Wrapper: SSP/publisher fetches the actual VAST from vastUrl.
        if (creative.getVastUrl() != null && !creative.getVastUrl().isBlank()) {
            return buildVastWrapper(adId, creative.getVastUrl(), clickUrl, impression);
        }

        // VAST Inline: direct MP4/WebM file — build a complete Inline VAST 2.0 node.
        if (creative.getVideoUrl() != null && !creative.getVideoUrl().isBlank()) {
            int duration = creative.getDurationSec() != null ? creative.getDurationSec() : 30;
            return buildVastInline(adId, creative.getVideoUrl(), duration, clickUrl, impression);
        }

        // Fallback: empty wrapper (should not reach here if isServable() is correct)
        log.warn("[AdMarkup] Video creative {} has neither vastUrl nor videoUrl", creative.getId());
        return buildVastWrapper(adId, "", clickUrl, impression);
    }

    /** VAST 2.0 Wrapper — delegates rendering to the upstream VAST URI. */
    private String buildVastWrapper(String adId, String vastUri,
                                    String clickUrl, String impression) {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" +
               "<VAST version=\"2.0\">" +
               "<Ad id=\"" + adId + "\">" +
               "<Wrapper>" +
               "<AdSystem>CentralDSP</AdSystem>" +
               "<VASTAdTagURI><![CDATA[" + vastUri + "]]></VASTAdTagURI>" +
               "<Impression><![CDATA[" + impression + "]]></Impression>" +
               "<Creatives><Creative>" +
               "<Linear><VideoClicks>" +
               "<ClickThrough><![CDATA[" + clickUrl + "]]></ClickThrough>" +
               "</VideoClicks></Linear>" +
               "</Creative></Creatives>" +
               "</Wrapper></Ad></VAST>";
    }

    /** VAST 2.0 Inline — embeds the MP4 directly, no second fetch needed. */
    private String buildVastInline(String adId, String videoUrl, int durationSec,
                                   String clickUrl, String impression) {
        // Duration in HH:MM:SS format
        String dur = String.format("%02d:%02d:%02d",
                durationSec / 3600, (durationSec % 3600) / 60, durationSec % 60);

        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" +
               "<VAST version=\"2.0\">" +
               "<Ad id=\"" + adId + "\">" +
               "<InLine>" +
               "<AdSystem>CentralDSP</AdSystem>" +
               "<AdTitle>Ad</AdTitle>" +
               "<Impression><![CDATA[" + impression + "]]></Impression>" +
               "<Creatives><Creative>" +
               "<Linear>" +
               "<Duration>" + dur + "</Duration>" +
               "<VideoClicks>" +
               "<ClickThrough><![CDATA[" + clickUrl + "]]></ClickThrough>" +
               "</VideoClicks>" +
               "<MediaFiles>" +
               "<MediaFile delivery=\"progressive\" type=\"video/mp4\"" +
               " width=\"1280\" height=\"720\">" +
               "<![CDATA[" + videoUrl + "]]>" +
               "</MediaFile>" +
               "</MediaFiles>" +
               "</Linear>" +
               "</Creative></Creatives>" +
               "</InLine></Ad></VAST>";
    }

    // ── Escape helpers ─────────────────────────────────────────────────────────
    private String escapeHtml(String s) {
        if (s == null) return "";
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&#x27;");
    }

    private String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "");
    }
}
