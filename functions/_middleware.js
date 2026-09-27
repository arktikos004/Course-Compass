/**
 * 把正式版的 pages.dev 網址導向自訂網域，讓對外只有一個網址。
 *
 * 為什麼要寫在 middleware 而不是用 Cloudflare 的 Redirect Rules：
 * Redirect Rules 只能作用在自己 zone 底下的流量，而 *.pages.dev 屬於 Cloudflare
 * 的網域、不在 sekinv.com 這個 zone 裡，規則抓不到。Pages Function 是唯一
 * 能攔到這段流量的位置。
 *
 * 刻意只導向「正式別名」course-compass-4pb.pages.dev，不動帶雜湊前綴的預覽網址
 * （例如 0ada9cec.course-compass-4pb.pages.dev）——那些是拿來確認某一次建置用的，
 * 全部導走就沒辦法在上線前預覽了。
 */

const CANONICAL_HOST = 'course.sekinv.com';

// 只比對這一個主機名；預覽網址是 <hash>.course-compass-4pb.pages.dev，不會命中
const PRODUCTION_ALIAS = 'course-compass-4pb.pages.dev';

export async function onRequest(context) {
    const url = new URL(context.request.url);

    if (url.hostname === PRODUCTION_ALIAS) {
        url.hostname = CANONICAL_HOST;
        // 301：這是永久搬遷，讓瀏覽器與搜尋引擎記住新位置。
        // 路徑與查詢字串原樣保留，深層連結不會斷。
        return Response.redirect(url.toString(), 301);
    }

    return context.next();
}
