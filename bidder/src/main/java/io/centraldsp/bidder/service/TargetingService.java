package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import io.centraldsp.bidder.model.campaign.CreativeDTO;
import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.Device;
import io.centraldsp.bidder.model.ortb.Impression;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.DayOfWeek;
import java.time.ZonedDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

/**
 * Evaluates targeting rules for a campaign against an incoming BidRequest.
 * All checks must pass (AND logic) for the campaign to be eligible.
 */
@Slf4j
@Service
public class TargetingService {

    /**
     * Check if campaign is eligible for this bid request.
     * @return matched creative, or empty if targeting fails
     */
    public Optional<CreativeDTO> match(CampaignDTO campaign, BidRequest request, Impression imp) {
        Device device = request.getDevice();

        // ── 1. Geo country ────────────────────────────────────────────────────
        if (!campaign.getGeoCountries().isEmpty()) {
            String country = resolveCountry(device);
            if (country == null || !campaign.getGeoCountries().contains(country.toUpperCase())) {
                return Optional.empty();
            }
        }

        // ── 2. Device type ────────────────────────────────────────────────────
        if (campaign.getDeviceTypes() != null && !campaign.getDeviceTypes().isEmpty()) {
            if (device == null || device.getDevicetype() == null ||
                    !campaign.getDeviceTypes().contains(device.getDevicetype())) {
                return Optional.empty();
            }
        }

        // ── 3. OS targeting ──────────────────────────────────────────────────
        if (campaign.getOsList() != null && !campaign.getOsList().isEmpty()) {
            String os = device != null ? normalize(device.getOs()) : null;
            if (os == null || !campaign.getOsList().contains(os)) {
                return Optional.empty();
            }
        }

        // ── 4. Site whitelist / blacklist ────────────────────────────────────
        String domain = resolveDomain(request);
        if (domain != null) {
            List<String> whitelist = campaign.getSiteWhitelist();
            if (whitelist != null && !whitelist.isEmpty() && !whitelist.contains(domain)) {
                return Optional.empty();
            }
            List<String> blacklist = campaign.getSiteBlacklist();
            if (blacklist != null && blacklist.contains(domain)) {
                return Optional.empty();
            }
        }

        // ── 5. Hour schedule ─────────────────────────────────────────────────
        if (campaign.getHourSchedule() != null) {
            ZonedDateTime now = ZonedDateTime.now(ZoneOffset.UTC);
            int day  = now.getDayOfWeek().getValue() - 1; // 0=Mon..6=Sun
            int hour = now.getHour();
            if (!campaign.getHourSchedule()[day][hour]) {
                return Optional.empty();
            }
        }

        // ── 6. Bid floor check ───────────────────────────────────────────────
        double floor = imp.getBidfloor() != null ? imp.getBidfloor() : 0.0;
        double ourFloor = campaign.getBidFloor() != null ? campaign.getBidFloor() : 0.0;
        if (ourFloor < floor) {
            // Our minimum is below the SSP floor — skip unless bid_price covers it
            if (campaign.getBidPrice() < floor) {
                return Optional.empty();
            }
        }

        // ── 7. Creative format match ─────────────────────────────────────────
        if (campaign.getCreatives() == null) return Optional.empty();
        for (CreativeDTO creative : campaign.getCreatives()) {
            if (!"approved".equals(creative.getStatus())) continue;
            if (imp.getBanner() != null && "banner".equals(creative.getFormat())) {
                // Check size compatibility
                if (sizeMatches(creative, imp)) return Optional.of(creative);
            } else if (imp.getNativeAd() != null && "native".equals(creative.getFormat())) {
                return Optional.of(creative);
            } else if (imp.getVideo() != null && "video".equals(creative.getFormat())) {
                return Optional.of(creative);
            }
        }

        return Optional.empty();
    }

    private String resolveCountry(Device device) {
        if (device == null) return null;
        if (device.getGeo() != null && device.getGeo().getCountry() != null) {
            return device.getGeo().getCountry();
        }
        return null;
    }

    private String resolveDomain(BidRequest request) {
        if (request.getSite() != null) return request.getSite().getDomain();
        if (request.getApp() != null)  return request.getApp().getBundle();
        return null;
    }

    private String normalize(String s) {
        return s == null ? null : s.toLowerCase().trim();
    }

    private boolean sizeMatches(CreativeDTO creative, Impression imp) {
        if (creative.getWidth() == null || creative.getHeight() == null) return true;
        if (imp.getBanner() == null) return false;
        Banner banner = imp.getBanner();
        // Check exact size
        if (creative.getWidth().equals(banner.getW()) &&
            creative.getHeight().equals(banner.getH())) return true;
        // Check format list
        if (banner.getFormat() != null) {
            return banner.getFormat().stream().anyMatch(
                f -> creative.getWidth().equals(f.getW()) &&
                     creative.getHeight().equals(f.getH()));
        }
        return false;
    }

    // Workaround: import Banner in same file since it's an inner class
    private io.centraldsp.bidder.model.ortb.Banner Banner = null;
    { Banner = null; } // suppress unused warning
}
