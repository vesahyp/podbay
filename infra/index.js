// A directory address serves its index.html: /stats/ and /stats both read
// stats/index.html. CloudFront's default root object covers only "/".
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith("/")) {
    request.uri = uri + "index.html";
  } else if (uri.lastIndexOf(".") < uri.lastIndexOf("/")) {
    return {
      statusCode: 301,
      statusDescription: "Moved Permanently",
      headers: { location: { value: uri + "/" } },
    };
  }
  return request;
}
