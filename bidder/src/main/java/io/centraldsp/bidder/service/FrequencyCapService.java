package io.centraldsp.bidder.service;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;

/**
 * Frequency capping via Redis INCR.
 * Key: cdsp:freq:{campaignId}:{userId}
 * TTL: 24 hours (rolling window per day).
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class FrequencyCapService {

    private final JedisPool jedisPool;

    @Value("${redis.db:1}")
    private int redisDb;

    @Value("${frequency.cap.max:5}")
    private int maxImpressions;

    @Value("${frequency.cap.redis.ttl:86400}")
    private long ttlSeconds;

    private static final String KEY_PREFIX = "cdsp:freq:";

    /**
     * Check if user has exceeded frequency cap for this campaign.
     * @return true if user can see the ad (not capped)
     */
    public boolean canServe(Long campaignId, String userId) {
        if (userId == null || userId.isEmpty()) return true; // unknown user — allow
        String key = KEY_PREFIX + campaignId + ":" + userId;
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            int count = val != null ? Integer.parseInt(val) : 0;
            return count < maxImpressions;
        } catch (Exception e) {
            log.warn("[FrequencyCap] Check failed: {}", e.getMessage());
            return true; // fail-open
        }
    }

    /** Increment impression counter for user/campaign after win. */
    public void recordImpression(Long campaignId, String userId) {
        if (userId == null || userId.isEmpty()) return;
        String key = KEY_PREFIX + campaignId + ":" + userId;
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            long count = jedis.incr(key);
            if (count == 1) {
                jedis.expire(key, ttlSeconds);
            }
        } catch (Exception e) {
            log.warn("[FrequencyCap] recordImpression failed: {}", e.getMessage());
        }
    }
}
